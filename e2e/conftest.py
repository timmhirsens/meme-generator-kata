"""Starts a real uvicorn process on a free port; tests talk to it over HTTP or through a browser."""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import ViewportSize

FIXTURES = Path(__file__).parent / "fixtures"

# The page shows a meme at most 640 px wide. At 800 × 1000 px the form and a square meme fit
# without scrolling; videos get the same size instead of Playwright's default 800 × 450.
VIEWPORT: ViewportSize = {"width": 800, "height": 1000}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    """Also pytest-playwright's base URL, so `page.goto("/")` opens this server."""
    external_url = os.environ.get("MEME_TEST_BASE_URL")
    if external_url:
        url = external_url.rstrip("/")
        response = httpx.get(f"{url}/health", timeout=10)
        response.raise_for_status()
        yield url
        return
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "meme_generator.app:app", "--port", str(port)]
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                if httpx.get(f"{url}/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.1)
        yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """pytest-playwright's browser context options, with our viewport and video size."""
    return {**browser_context_args, "viewport": VIEWPORT, "record_video_size": VIEWPORT}


@pytest.fixture(scope="session")
def client(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=30) as http:
        yield http


@pytest.fixture
def jpeg_path() -> Path:
    return FIXTURES / "gradient-800x600.jpg"


@pytest.fixture
def jpeg(jpeg_path: Path) -> bytes:
    return jpeg_path.read_bytes()
