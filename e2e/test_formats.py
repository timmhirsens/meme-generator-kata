"""Format identity and transparency through the real HTTP/browser interfaces."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw
from playwright.sync_api import Page, expect


def encoded_image(format: str, mode: str = "RGBA") -> bytes:
    source = Image.new("RGBA", (400, 400), (20, 100, 150, 0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((30, 100, 370, 300), fill=(30, 160, 200, 128))
    draw.rectangle((80, 160, 320, 240), fill=(180, 80, 120, 255))
    source = source.convert(mode)
    if format == "PNG" and mode == "RGB":
        source.info["transparency"] = (20, 100, 150)
    if format == "PNG" and mode == "L":
        source.info["transparency"] = 82
    out = io.BytesIO()
    source.save(out, format=format)
    return out.getvalue()


@pytest.mark.parametrize(
    ("format", "mode"),
    [
        ("PNG", "RGBA"),
        ("PNG", "P"),
        ("PNG", "LA"),
        ("PNG", "RGB"),
        ("PNG", "L"),
        ("WEBP", "RGBA"),
        ("WEBP", "RGB"),
    ],
)
def test_format_identity_and_alpha(client: httpx.Client, format: str, mode: str) -> None:
    encoded = encoded_image(format, mode)
    source = Image.open(io.BytesIO(encoded)).convert("RGBA")
    if format == "PNG" or mode == "RGBA":
        assert source.getchannel("A").getextrema() == (0, 255)
    if mode in {"RGBA", "LA", "P"}:
        assert source.getchannel("A").getpixel((40, 200)) == 128
    # Deliberately misleading filename/MIME: the decoded format is authoritative.
    response = client.post(
        "/memes",
        files={"image": ("wrong.jpg", encoded, "image/jpeg")},
        data={"top_text": "FORMAT TEST"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == f"image/{format.lower()}"
    decoded = Image.open(io.BytesIO(response.content))
    assert decoded.format == format
    assert decoded.size == (400, 400)
    result = decoded.convert("RGBA")
    middle = (0, 100, 400, 400)
    assert (
        source.getchannel("A").crop(middle).tobytes()
        == result.getchannel("A").crop(middle).tobytes()
    )
    if format == "PNG":
        assert source.crop(middle).tobytes() == result.crop(middle).tobytes()
    assert (
        ImageChops.difference(source.convert("RGB"), result.convert("RGB"))
        .crop((0, 0, 400, 100))
        .getbbox()
        is not None
    )


def test_bmp_remains_unsupported(client: httpx.Client) -> None:
    response = client.post(
        "/memes", files={"image": ("a.bmp", encoded_image("BMP", "RGB"))}, data={"top_text": "BMP"}
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_image"


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
def test_page_accepts_and_displays_format(page: Page, tmp_path: Path, format: str) -> None:
    path = tmp_path / f"alpha.{format.lower()}"
    path.write_bytes(encoded_image(format))
    page.goto("/")
    upload = page.get_by_label("Image")
    accept = upload.get_attribute("accept") or ""
    assert {"image/jpeg", "image/png", "image/webp"} <= set(accept.split(","))
    upload.set_input_files(path)
    page.get_by_label("Top text").fill(f"{format} WITH ALPHA")
    with page.expect_response("**/memes") as response_info:
        page.get_by_role("button", name="Create meme").click()
    assert response_info.value.headers["content-type"] == f"image/{format.lower()}"
    expect(page.get_by_role("img", name="Meme")).to_be_visible()
    expect(page.get_by_role("img", name="Meme")).to_have_js_property("naturalWidth", 400)
