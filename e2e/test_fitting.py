"""Independent caption fitting exercised through HTTP and the page."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageChops
from playwright.sync_api import Page, expect

LONG_CAPTION = (
    "A long caption with words that must wrap and shrink independently of the short caption below. "
    "Every word must stay inside the picture without cutting."
)


@pytest.fixture
def square(tmp_path: Path) -> Path:
    path = tmp_path / "square.jpg"
    Image.new("RGB", (400, 400), (60, 120, 160)).save(path)
    return path


@pytest.mark.parametrize("long_field", ["top_text", "bottom_text"])
def test_long_caption_fits_without_shrinking_other_caption(
    client: httpx.Client, square: Path, long_field: str
) -> None:
    assert len(LONG_CAPTION) == 150
    short_field = "bottom_text" if long_field == "top_text" else "top_text"
    outputs: list[Image.Image] = []
    for text in [LONG_CAPTION, "SHORT"]:
        response = client.post(
            "/memes",
            files={"image": ("a.jpg", square.read_bytes())},
            data={long_field: text, short_field: "CONTROL"},
        )
        assert response.status_code == 200
        outputs.append(Image.open(io.BytesIO(response.content)).convert("RGB"))
    # The background's luminance is below 128; threshold without dithering to find text.
    white = outputs[0].convert("1", dither=Image.Dither.NONE)
    band = (0, 0, 400, 200) if long_field == "top_text" else (0, 200, 400, 400)
    box = white.crop(band).getbbox()
    assert box is not None
    assert 18 <= box[0] < box[2] <= 382
    assert 30 < box[3] - box[1] <= 100  # multiple lines, within quarter-height
    other_band = (0, 200, 400, 400) if long_field == "top_text" else (0, 0, 400, 200)
    assert (
        ImageChops.difference(outputs[0].crop(other_band), outputs[1].crop(other_band)).getbbox()
        is None
    )


@pytest.mark.parametrize("text", ["W" * 60, "HELLO\n" * 20])
def test_unfittable_caption_is_422(client: httpx.Client, square: Path, text: str) -> None:
    response = client.post(
        "/memes", files={"image": ("a.jpg", square.read_bytes())}, data={"top_text": text}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "caption_does_not_fit"


def test_single_word_can_shrink_without_splitting(client: httpx.Client, square: Path) -> None:
    response = client.post(
        "/memes", files={"image": ("a.jpg", square.read_bytes())}, data={"top_text": "i" * 60}
    )
    assert response.status_code == 200
    meme = Image.open(io.BytesIO(response.content))
    box = meme.convert("1", dither=Image.Dither.NONE).getbbox()
    assert box is not None
    assert 18 <= box[0] < box[2] <= 382
    assert box[3] - box[1] < 30  # one line, not a broken word


@pytest.mark.parametrize(("height", "status"), [(400, 200), (40, 422)])
def test_initial_scale_and_minimum_font(client: httpx.Client, height: int, status: int) -> None:
    source = io.BytesIO()
    Image.new("RGB", (400, height), (60, 120, 160)).save(source, format="JPEG")
    response = client.post(
        "/memes", files={"image": ("a.jpg", source.getvalue())}, data={"top_text": "I"}
    )
    assert response.status_code == status
    if status == 200:
        meme = Image.open(io.BytesIO(response.content))
        box = meme.convert("1", dither=Image.Dither.NONE).getbbox()
        assert box is not None
        assert 30 <= box[3] - box[1] <= 42  # starts near 40 px, not the 14 px floor
    else:
        assert response.json()["error"] == "caption_does_not_fit"


def test_fitting_error_and_recovery(page: Page, square: Path) -> None:
    page.goto("/")
    page.get_by_label("Image").set_input_files(square)
    page.get_by_label("Top text").fill("W" * 60)
    page.get_by_role("button", name="Create meme").click()
    expect(page.get_by_role("alert")).to_contain_text("Error 422")
    expect(page.get_by_role("alert")).to_contain_text("fit")
    page.get_by_label("Top text").fill(LONG_CAPTION)
    page.get_by_label("Bottom text").fill("SHORT")
    page.get_by_role("button", name="Create meme").click()
    expect(page.get_by_role("img", name="Meme")).to_be_visible()
