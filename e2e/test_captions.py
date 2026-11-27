"""Caption contracts through the real server and browser."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageChops
from playwright.sync_api import Page, expect


@pytest.mark.parametrize("field", ["top_text", "bottom_text"])
@pytest.mark.parametrize(
    ("text", "status", "message"),
    [
        ("hello 😀🚀😀", 422, "😀"),
        ("hello\tthere", 400, "control"),
        ("a" * 201, 400, "200"),
        (" " + "a" * 200, 400, "200"),
        ("hello\x00", 400, "control"),
        ("hello\rthere", 400, "control"),
    ],
)
def test_caption_errors(
    client: httpx.Client, jpeg: bytes, field: str, text: str, status: int, message: str
) -> None:
    response = client.post("/memes", files={"image": ("a.jpg", jpeg)}, data={field: text})
    assert response.status_code == status
    assert set(response.json()) == {"error", "message"}
    assert message in response.json()["message"]
    if "😀" in text:
        assert "🚀" in response.json()["message"]


def test_count_before_uppercase_and_supported_accents(client: httpx.Client, jpeg: bytes) -> None:
    for text in [" " * 199 + "ß", "i " * 100, "ÄÖÜ éà ç Ł ø"]:
        response = client.post("/memes", files={"image": ("a.jpg", jpeg)}, data={"top_text": text})
        assert response.status_code == 200


def test_explicit_newline_changes_image(client: httpx.Client, jpeg: bytes) -> None:
    images: list[Image.Image] = []
    for text in ["HELLO WORLD", "HELLO\nWORLD"]:
        response = client.post("/memes", files={"image": ("a.jpg", jpeg)}, data={"top_text": text})
        assert response.status_code == 200
        images.append(Image.open(io.BytesIO(response.content)).convert("RGB"))
    assert ImageChops.difference(*images).getbbox() is not None


def test_multiline_and_readable_errors(page: Page, jpeg_path: Path) -> None:
    page.goto("/")
    page.get_by_label("Image").set_input_files(jpeg_path)
    top = page.get_by_label("Top text")
    assert top.evaluate("el => el.tagName") == "TEXTAREA"
    for text, message in [("hello 😀", "😀"), ("a" * 201, "200"), ("hello\tthere", "control")]:
        top.fill(text)
        page.get_by_role("button", name="Create meme").click()
        expect(page.get_by_role("alert")).to_contain_text(message)
    top.fill("HELLO\nWORLD")
    page.get_by_label("Bottom text").fill("TWO LINES\nWORK")
    page.get_by_role("button", name="Create meme").click()
    expect(page.get_by_role("img", name="Meme")).to_be_visible()
