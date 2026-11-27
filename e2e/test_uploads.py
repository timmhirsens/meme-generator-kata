"""Privacy boundaries against a real server that refuses filesystem writes."""

import io
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Iterator

import httpx
import pytest
from PIL import Image

from conftest import free_port

LIMIT = 10 * 1024 * 1024
GUARDED_SERVER = """
import os, sys, uvicorn
sys.dont_write_bytecode = True
def guard(event, args):
    if event == 'open' and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
        print('DISK_WRITE_ATTEMPT', file=sys.stderr, flush=True)
        raise RuntimeError('Filesystem writes forbidden')
sys.addaudithook(guard)
uvicorn.run('meme_generator.app:app', port=int(sys.argv[1]), access_log=False)
"""


@pytest.fixture(scope="module")
def private_client() -> Iterator[httpx.Client]:
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-c", GUARDED_SERVER, str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
                raise RuntimeError("guarded server failed to start")
            yield client
    finally:
        server.terminate()
        _, stderr = server.communicate(timeout=10)
        assert b"DISK_WRITE_ATTEMPT" not in stderr, stderr.decode()


def picture(format: str = "JPEG", size: tuple[int, int] = (400, 400)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "teal").save(out, format=format)
    return out.getvalue()


@pytest.mark.parametrize("length", [None, LIMIT, LIMIT + 1, 12 * 1024 * 1024])
@pytest.mark.parametrize("chunked", [False, True])
def test_upload_size_boundaries_stay_in_memory(
    private_client: httpx.Client, length: int | None, chunked: bool
) -> None:
    image = picture()
    if length is not None:
        image += b"\0" * (length - len(image))
    request = private_client.build_request(
        "POST", "/memes", files={"image": ("private-name.jpg", image)}, data={"top_text": "hello"}
    )
    if chunked:
        body = request.read()
        del request.headers["content-length"]
        request.headers["transfer-encoding"] = "chunked"
        request.stream = httpx.ByteStream(body)
    response = private_client.send(request)
    assert response.status_code == (413 if length and length > LIMIT else 200), response.text[:200]


def bomb() -> bytes:
    data = bytearray(picture("PNG"))
    data[16:24] = struct.pack(">II", 20000, 10000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    return bytes(data)


@pytest.mark.parametrize("payload", [b"broken", picture("BMP"), bomb()])
def test_rejected_images_never_write(private_client: httpx.Client, payload: bytes) -> None:
    response = private_client.post(
        "/memes", files={"image": ("private-name", payload)}, data={"top_text": "private caption"}
    )
    assert response.status_code in (415, 422)


def test_multiple_files_are_rejected_without_writes(private_client: httpx.Client) -> None:
    response = private_client.post(
        "/memes",
        files=[("image", ("one.jpg", picture())), ("image", ("two.jpg", picture()))],
        data={"top_text": "hello"},
    )
    assert response.status_code == 400


def test_tiny_image_never_writes(private_client: httpx.Client) -> None:
    response = private_client.post(
        "/memes", files={"image": ("tiny.jpg", picture(size=(1, 1)))}, data={"top_text": "x"}
    )
    assert response.status_code == 422  # Minimum 14 px font cannot fit a 1 px image.
