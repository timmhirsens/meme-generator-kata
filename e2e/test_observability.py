"""Request correlation and privacy against the real CLI-launched server."""

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from conftest import free_port


def test_parallel_requests_are_correlated_without_content(tmp_path: Path, jpeg: bytes) -> None:
    port = free_port()
    log = tmp_path / "server.log"
    with log.open("w") as output:
        server = subprocess.Popen(
            [sys.executable, "-m", "meme_generator"],
            env={**os.environ, "MEME_PORT": str(port)},
            stdout=output,
            stderr=output,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                for _ in range(100):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("server did not start")

                def submit(number: int) -> None:
                    response = client.post(
                        "/memes?private-query-sentinel",
                        headers={"X-Request-ID": f"parallel-{number}"},
                        files={"image": ("private-filename-sentinel.jpg", jpeg)},
                        data={"top_text": "private-caption-sentinel" + "X" * number},
                    )
                    assert response.status_code == 200
                    assert response.headers["x-request-id"] == f"parallel-{number}"

                with ThreadPoolExecutor(max_workers=20) as pool:
                    list(pool.map(submit, range(20)))
                for supplied in ("good-ID-123", "a" * 64, "bad_id", "a" * 65, ""):
                    response = client.post("/memes", headers={"X-Request-ID": supplied})
                    identifier = response.headers["x-request-id"]
                    assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", identifier)
                    if supplied and "_" not in supplied and len(supplied) <= 64:
                        assert identifier == supplied
                    else:
                        assert identifier != supplied
                    assert response.json()["request_id"] == identifier
        finally:
            server.terminate()
            server.wait(timeout=10)
    text = log.read_text()
    for secret in (
        "private-query-sentinel",
        "private-filename-sentinel",
        "private-caption-sentinel",
    ):
        assert secret not in text
    assert jpeg.hex() not in text
    records = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    for number in range(20):
        matches = [record for record in records if record["request_id"] == f"parallel-{number}"]
        assert len(matches) == 1
        record = matches[0]
        assert record["top_length"] == len("private-caption-sentinel") + number
        assert (record["format"], record["width"], record["height"]) == ("JPEG", 800, 600)
        assert all(
            record[key] >= 0
            for key in ("queue_ms", "upload_ms", "decode_ms", "render_ms", "encode_ms", "total_ms")
        )
        assert set(record) == {
            "request_id",
            "status",
            "top_length",
            "bottom_length",
            "format",
            "width",
            "height",
            "queue_ms",
            "upload_ms",
            "decode_ms",
            "render_ms",
            "encode_ms",
            "total_ms",
        }


def test_cli_and_api_produce_identical_bytes(
    client: httpx.Client, jpeg_path: Path, jpeg: bytes, tmp_path: Path
) -> None:
    output = tmp_path / "meme.jpg"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "meme_generator",
            "render",
            str(jpeg_path),
            str(output),
            "--top-text",
            "hello",
            "--bottom-text",
            "world",
        ],
        check=True,
    )
    response = client.post(
        "/memes",
        files={"image": ("input.jpg", jpeg)},
        data={"top_text": "hello", "bottom_text": "world"},
    )
    assert response.status_code == 200
    assert output.read_bytes() == response.content
