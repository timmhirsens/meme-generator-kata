"""Real-HTTP F9 benchmark; run with `uv run python scripts/benchmark.py --help`."""

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
import platform
import random
import shlex
import subprocess
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import httpx
from PIL import Image

CAPTIONS = {"top_text": "ONE DOES NOT SIMPLY", "bottom_text": "SKIP THE BENCHMARK"}

# This helper runs inside the container, not the load generator. PID 1 must be
# our sole uvicorn worker. VmHWM catches peaks between the 100 ms RSS samples.
# stdin closes/stops the helper, so docker exec does not leave a sampler behind.
MEMORY_HELPER = """
import json, select, sys, time
from pathlib import Path
cmd = Path('/proc/1/cmdline').read_bytes().split(b'\\0')
if not any(b'uvicorn' in arg for arg in cmd) or b'--workers' not in cmd:
    raise RuntimeError('PID 1 must be uvicorn with explicit --workers 1')
if cmd[cmd.index(b'--workers') + 1] != b'1':
    raise RuntimeError('Only the single-worker image is supported')
while True:
    fields = dict(line.split(':', 1) for line in Path('/proc/1/status').read_text().splitlines())
    print(json.dumps({'rss_bytes': int(fields['VmRSS'].split()[0]) * 1024,
                      'hwm_bytes': int(fields['VmHWM'].split()[0]) * 1024}), flush=True)
    if select.select([sys.stdin], [], [], 0.1)[0]:
        break
"""


class MemorySampler:
    def __init__(self, docker: list[str], container: str):
        self.command = [*docker, "exec", "-i", container, "python", "-u", "-c", MEMORY_HELPER]
        self.samples: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.ready = threading.Event()
        self.phase = "warmup"

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()
        if not self.ready.wait(15) or self.errors or not self.samples:
            self.stop()
            raise RuntimeError(f"Memory sampler failed: {self.errors}")

    def read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                sample = json.loads(line)
                sample.update(phase=self.phase, monotonic=time.perf_counter())
                self.samples.append(sample)
                self.ready.set()
        except (ValueError, TypeError) as exc:
            self.errors.append(str(exc))
        finally:
            self.ready.set()

    def stop(self) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write("stop\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            pass
        self.process.stdin.close()
        self.process.wait(timeout=15)
        self.thread.join(timeout=5)
        if self.process.returncode != 0:
            self.errors.append(f"docker exec exited {self.process.returncode}")
        if not self.samples:
            self.errors.append("No worker RSS samples received")


def fixture() -> bytes:
    """Exactly 2,000,000 pixels, deterministic noisy RGB, JPEG quality 85."""
    pixels = random.Random(9).randbytes(2000 * 1000 * 3)
    with Image.frombytes("RGB", (2000, 1000), pixels) as image:
        output = io.BytesIO()
        image.save(output, "JPEG", quality=85, subsampling=2)
        return output.getvalue()


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile (no interpolation hiding a slow observation)."""
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


async def request(client: httpx.AsyncClient, jpeg: bytes, user: int) -> dict[str, Any]:
    started = time.perf_counter()
    status = None
    error = None
    try:
        response = await client.post(
            "/memes",
            files={"image": ("benchmark-2mp.jpg", jpeg, "image/jpeg")},
            data=CAPTIONS,
        )
        # post() has read the whole response body: upload, queuing, render,
        # encoding and complete download are all inside this interval.
        elapsed = (time.perf_counter() - started) * 1000
        status = response.status_code
        if status != 200:
            error = f"HTTP {status}"
        elif response.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            error = "Unexpected content type"
        elif not (
            response.content.startswith(b"\xff\xd8") and response.content.endswith(b"\xff\xd9")
        ):
            error = "Invalid JPEG envelope"
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        error = type(exc).__name__
    return {"user": user, "latency_ms": elapsed, "status": status, "error": error}


def summarize(records: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    latencies = [record["latency_ms"] for record in records]
    errors = Counter(record["error"] for record in records if record["error"])
    return {
        "requests": len(records),
        "errors": sum(errors.values()),
        "error_percent": 100 * sum(errors.values()) / len(records) if records else None,
        "error_kinds": dict(errors),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "max_ms": max(latencies) if latencies else None,
        "elapsed_seconds_including_drain": elapsed,
        "requests_per_second_including_drain": len(records) / elapsed,
    }


async def measure(args: argparse.Namespace, jpeg: bytes, memory: MemorySampler) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=args.users, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=10, limits=limits, trust_env=False
    ) as client:
        health = await client.get("/health")
        health.raise_for_status()
        # Fully decode one representative output, outside the timing phases.
        check = await client.post(
            "/memes", files={"image": ("check.jpg", jpeg)}, data={"top_text": "CHECK"}
        )
        check.raise_for_status()
        with Image.open(io.BytesIO(check.content)) as output:
            output.load()
            if output.format != "JPEG":
                raise RuntimeError("Representative response is not a decoded JPEG")
            output_size = output.size
        for _ in range(args.warmup):
            result = await request(client, jpeg, 0)
            if result["error"]:
                raise RuntimeError(f"Warmup failed: {result}")
        singles: list[dict[str, Any]] = []
        loads: list[dict[str, Any]] = []
        for run in range(1, args.repetitions + 1):
            memory.phase = f"single-{run}"
            started = time.perf_counter()
            records = [await request(client, jpeg, 0) for _ in range(args.single_requests)]
            singles.append({**summarize(records, time.perf_counter() - started), "raw": records})
            memory.phase = f"load-{run}"
            load_records: list[dict[str, Any]] = []
            started = time.perf_counter()
            deadline = started + args.duration

            async def user(number: int, until: float, results: list[dict[str, Any]]) -> None:
                while time.perf_counter() < until:
                    results.append(await request(client, jpeg, number))

            # Closed loop, no think time, no retries. Stop starting requests at
            # the deadline but include every in-flight completion/error (drain).
            await asyncio.gather(
                *(user(number, deadline, load_records) for number in range(args.users))
            )
            load_elapsed = time.perf_counter() - started
            loads.append({**summarize(load_records, load_elapsed), "raw": load_records})
            memory.phase = "between-runs"
            print(
                f"Run {run}: single p95={singles[-1]['p95_ms']:.2f} ms; "
                f"load p95={loads[-1]['p95_ms']:.2f} ms; errors={loads[-1]['errors']}",
                flush=True,
            )
        return {"single": singles, "load": loads, "decoded_output_size": output_size}


def report(data: dict[str, Any]) -> str:
    lines = [
        "# F9 performance measurement",
        "",
        f"UTC: {data['utc']}",
        f"Image: `{data['container']['Image']}`; name: `{data['settings']['container']}`.",
        f"Host: {data['host']}",
        "",
        "**Acceptance-sized run:** " + str(data["acceptance_sized"]),
        "**Overall acceptance passed:** " + str(data["acceptance_passed"]),
        "",
        "A shortened smoke run is not performance acceptance evidence.",
        "",
        "| Run | Single p95 (<300 ms) | Single errors | Load p95 (<1000 ms) | Load errors (0) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for number, (single, load) in enumerate(zip(data["single"], data["load"], strict=True), 1):
        lines.append(
            f"| {number} | {single['p95_ms']:.2f} ms | {single['errors']}/{single['requests']} | "
            f"{load['p95_ms']:.2f} ms | "
            f"{load['errors']}/{load['requests']} ({load['error_percent']:.3f}%) |"
        )
    lines.extend(["", "Individual checks (short runs are still not acceptance):", ""])
    lines.extend(f"- {name}: {passed}" for name, passed in data["checks"].items())
    lines.extend(
        [
            "",
            f"Median single-run p95: {median(run['p95_ms'] for run in data['single']):.2f} ms.",
            f"Median load-run p95: {median(run['p95_ms'] for run in data['load']):.2f} ms.",
            f"Peak sampled worker RSS: {data['peak_rss_bytes'] / 1_000_000:.2f} MB.",
            f"Worker lifetime RSS high-water mark: {data['peak_hwm_bytes'] / 1_000_000:.2f} MB "
            "(limit <=300 MB; conservative, includes warmup/earlier worker lifetime).",
            "",
            "## Method and limitations",
            "",
            "Deterministic 2000×1000 RGB noise JPEG, quality 85, seed 9; same two captions "
            "for every timed request. This is one explicit workload, not all possible images.",
            "Client and server share this host; no TLS, proxy or internet latency. "
            "The load generator consumes host resources too. Stop other work before acceptance.",
            "Closed-loop users, one outstanding POST per user, no think time or retries. "
            "Timer covers complete upload and response download, including client queuing. "
            "p95 uses nearest rank over ALL attempts, including failures/timeouts. "
            "In-flight requests drain after the deadline and are included, not discarded.",
            "Warmup and a fully decoded representative JPEG are outside timed phases. "
            "Each timed response is checked for status, MIME type and JPEG envelope; "
            "it is not decoded in the latency interval.",
            "RSS is read inside the container from /proc/1/status every 100 ms. "
            "VmHWM additionally captures between-sample peaks. Only the explicit single-worker "
            "image is supported; this is not container cgroup memory or an average per worker.",
            "Acceptance requires the read-only non-root image, >=100 single requests/run, "
            ">=60 s load/run, exactly 20 users, >=3 repetitions, zero errors in both phases, "
            "each run below the latency thresholds, and worker HWM <=300,000,000 bytes. "
            "Median p95 is descriptive only; it does not hide a failed repetition.",
            "The accompanying JSON contains settings, raw attempts, memory samples, "
            "image identity, limits, environment, and pass/fail for each criterion. "
            "This report does not assert that the functional suite passed; run that separately.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--container", required=True, help="Single-worker runtime container name")
    parser.add_argument("--docker-command", default="docker", help="For example: 'sudo docker'")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--single-requests", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--output", type=Path, required=True, help="Raw JSON; also writes sibling .md"
    )
    args = parser.parse_args()
    if min(args.duration, args.users, args.single_requests, args.warmup, args.repetitions) <= 0:
        parser.error("durations and counts must be positive")
    docker = shlex.split(args.docker_command)
    inspected = json.loads(
        subprocess.check_output([*docker, "inspect", args.container], text=True)
    )[0]
    config = inspected["HostConfig"]
    container = {
        "Id": inspected["Id"],
        "Image": inspected["Image"],
        "User": inspected["Config"]["User"],
        "ReadonlyRootfs": config["ReadonlyRootfs"],
        "Memory": config["Memory"],
        "NanoCpus": config["NanoCpus"],
        "NetworkMode": config["NetworkMode"],
        "RestartCount": inspected["RestartCount"],
    }
    jpeg = fixture()
    data: dict[str, Any] = {
        "utc": datetime.now(UTC).isoformat(),
        "settings": {**vars(args), "output": str(args.output)},
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "load_average_at_start": os.getloadavg(),
            "httpx": httpx.__version__,
        },
        "container": container,
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input": {
            "pixels": 2_000_000,
            "bytes": len(jpeg),
            "sha256": hashlib.sha256(jpeg).hexdigest(),
            "captions": CAPTIONS,
            "seed": 9,
            "quality": 85,
            "subsampling": 2,
        },
    }
    memory = MemorySampler(docker, args.container)
    memory.start()
    try:
        data.update(asyncio.run(measure(args, jpeg, memory)))
    finally:
        memory.stop()
    data["memory_samples"] = memory.samples
    data["memory_errors"] = memory.errors
    data["peak_rss_bytes"] = max(sample["rss_bytes"] for sample in memory.samples)
    data["peak_hwm_bytes"] = max(sample["hwm_bytes"] for sample in memory.samples)
    after = json.loads(subprocess.check_output([*docker, "inspect", args.container], text=True))[0]
    data["checks"] = {
        "single_latency": all(run["p95_ms"] < 300 for run in data["single"]),
        "load_latency": all(run["p95_ms"] < 1000 for run in data["load"]),
        "zero_errors": all(run["errors"] == 0 for run in [*data["single"], *data["load"]]),
        "worker_memory": data["peak_hwm_bytes"] <= 300_000_000 and not memory.errors,
        "read_only": container["ReadonlyRootfs"],
        "non_root": container["User"].split(":")[0] not in ("", "0", "root"),
        "no_restart": after["RestartCount"] == container["RestartCount"]
        and after["State"]["Running"],
    }
    data["acceptance_sized"] = (
        args.duration >= 60
        and args.users == 20
        and args.single_requests >= 100
        and args.repetitions >= 3
    )
    data["acceptance_passed"] = data["acceptance_sized"] and all(data["checks"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    args.output.with_suffix(".md").write_text(report(data))
    print(
        json.dumps(
            {"checks": data["checks"], "acceptance_passed": data["acceptance_passed"]}, indent=2
        )
    )
    # Even diagnostic runs fail loudly on a missed target. A successful short
    # run still cannot claim acceptance: acceptance_sized/pass remain false.
    return 0 if all(data["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
