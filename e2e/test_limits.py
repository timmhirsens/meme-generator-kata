"""Resource limits tested over HTTP, including actual admission contention."""

import io
import socket
import struct
import time
import zlib
from contextlib import ExitStack
from urllib.parse import urlparse

import httpx
import pytest
from PIL import Image, ImageDraw


@pytest.mark.parametrize("width,status", [(5000, 415), (5001, 422)])
def test_pixel_limit_is_checked_before_decoding(
    client: httpx.Client, width: int, status: int
) -> None:
    # A valid PNG header, but deliberately missing compressed pixel data. Decoding
    # would fail with 415; the dimensions must instead trigger 422 first.
    ihdr = b"IHDR" + struct.pack(">IIBBBBB", width, 5000, 8, 2, 0, 0, 0)
    image = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + ihdr
    image += struct.pack(">I", zlib.crc32(ihdr)) + b"\x00\x00\x00\x00IDAT"
    response = client.post("/memes", files={"image": ("large.png", image)}, data={"top_text": "x"})
    assert response.status_code == status
    if status == 422:
        assert response.json()["error"] == "image_too_large"


def test_output_has_a_bounded_long_side(client: httpx.Client) -> None:
    out = io.BytesIO()
    Image.new("RGB", (2600, 1800), "teal").save(out, "JPEG")
    response = client.post(
        "/memes", files={"image": ("large.jpg", out.getvalue())}, data={"top_text": "hello"}
    )
    assert response.status_code == 200
    result = Image.open(io.BytesIO(response.content))
    assert result.size == (1600, 1108)


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
def test_resized_png_keeps_alpha_aware_lanczos_pixels(client: httpx.Client, mode: str) -> None:
    image = Image.new("RGBA", (2600, 1800), (190, 70, 25, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon([(200, 100), (2300, 1700), (800, 1600)], fill=(20, 80, 220, 64))
    draw.rectangle((1300, 0, 1900, 1800), fill=(40, 190, 90, 255))
    image = image.convert(mode)
    out = io.BytesIO()
    image.save(out, "PNG")
    # Independent reference: Pillow's original alpha-aware resize. The memory
    # optimization must not replace premultiplied Lanczos with channel-wise resize.
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    expected = image.convert("RGBA")
    response = client.post(
        "/memes", files={"image": ("large.png", out.getvalue())}, data={"top_text": "hello"}
    )
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.size == expected.size == (1600, 1108)
        assert result.mode == "RGBA"
        center = (0, 300, 1600, 800)
        assert result.crop(center).tobytes() == expected.crop(center).tobytes()


def test_overload_times_out_then_recovers(client: httpx.Client, base_url: str, jpeg: bytes) -> None:
    url = urlparse(base_url)
    assert url.hostname and url.port
    # Keep all three admission slots busy receiving unfinished uploads. This forces
    # contention deterministically without monkeypatches or making rendering slow.
    with ExitStack() as held:
        for _ in range(3):
            slow = held.enter_context(
                socket.create_connection((url.hostname, url.port), timeout=10)
            )
            slow.sendall(
                b"POST /memes HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: multipart/form-data; boundary=x\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
                b"5\r\n--x\r\n\r\n"
            )
        time.sleep(0.1)
        started = time.monotonic()
        response = client.post(
            "/memes", files={"image": ("ok.jpg", jpeg)}, data={"top_text": "hello"}
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
        assert 2.5 < elapsed < 6
        assert client.get("/health").status_code == 200
    # Disconnect releases the slot; the next normal user succeeds.
    response = client.post("/memes", files={"image": ("ok.jpg", jpeg)}, data={"top_text": "hello"})
    assert response.status_code == 200
