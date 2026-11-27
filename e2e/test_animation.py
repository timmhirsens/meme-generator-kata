"""Animation metadata and every rendered frame, through the real API."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw
from playwright.sync_api import Page, expect


def animation(count: int, loop: int | None = 3) -> bytes:
    frames = [Image.new("RGB", (400, 300), (20 + i % 100, 100, 150)) for i in range(count)]
    out = io.BytesIO()
    options = {} if loop is None else {"loop": loop}
    frames[0].save(
        out,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[40 + (i % 3) * 30 for i in range(count)],
        disposal=2,
        **options,
    )
    return out.getvalue()


@pytest.mark.parametrize("loop", [None, 0, 3])
def test_each_gif_frame_gets_caption_and_retains_timing(
    client: httpx.Client, loop: int | None
) -> None:
    payload = animation(3, loop)
    response = client.post(
        "/memes",
        files={"image": ("input.gif", payload)},
        data={"top_text": "hello", "bottom_text": "world"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"
    source, result = Image.open(io.BytesIO(payload)), Image.open(io.BytesIO(response.content))
    assert getattr(result, "n_frames", 1) == 3
    assert result.info.get("loop") == loop
    masks: list[bytes] = []
    for i, expected_duration in enumerate((40, 70, 100)):
        source.seek(i)
        result.seek(i)
        assert result.info["duration"] == expected_duration
        before, after = source.convert("RGB"), result.convert("RGB")
        assert (
            ImageChops.difference(
                before.crop((0, 100, 400, 200)), after.crop((0, 100, 400, 200))
            ).getbbox()
            is None
        )
        # Both top and bottom contain black outline and white fill on every frame.
        for band in ((0, 0, 400, 75), (0, 225, 400, 300)):
            colors = set(after.crop(band).get_flattened_data())
            assert (0, 0, 0) in colors and (255, 255, 255) in colors
        masks.append(bytes(255 if pixel > 240 else 0 for pixel in after.convert("L").tobytes()))
    assert masks[0] == masks[1] == masks[2]


@pytest.mark.parametrize("count,status", [(300, 200), (301, 422)])
def test_gif_frame_limit(client: httpx.Client, count: int, status: int) -> None:
    response = client.post(
        "/memes", files={"image": ("input.gif", animation(count))}, data={"top_text": "hello"}
    )
    assert response.status_code == status


def test_transparent_delta_frames_keep_their_composited_contents(client: httpx.Client) -> None:
    frames: list[Image.Image] = []
    for x, color in ((60, "red"), (130, "blue"), (200, "lime")):
        frame = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
        ImageDraw.Draw(frame).rectangle((x, 120, x + 60, 180), fill=color)
        frames.append(frame)
    payload = io.BytesIO()
    frames[0].save(
        payload,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[40, 70, 100],
        loop=2,
        disposal=[1, 2, 3],
        optimize=True,
    )
    response = client.post(
        "/memes", files={"image": ("delta.gif", payload.getvalue())}, data={"top_text": "hello"}
    )
    assert response.status_code == 200
    source = Image.open(io.BytesIO(payload.getvalue()))
    result = Image.open(io.BytesIO(response.content))
    for index in range(3):
        source.seek(index)
        result.seek(index)
        before = source.convert("RGBA").crop((0, 100, 400, 200))
        after = result.convert("RGBA").crop((0, 100, 400, 200))
        assert before.tobytes() == after.tobytes()
        assert result.convert("RGBA").getpixel((0, 0)) == (0, 0, 0, 0)


def test_page_uploads_and_displays_gif(page: Page, tmp_path: Path) -> None:
    path = tmp_path / "animation.gif"
    path.write_bytes(animation(3))
    page.goto("/")
    upload = page.get_by_label("Image")
    assert "image/gif" in (upload.get_attribute("accept") or "").split(",")
    upload.set_input_files(path)
    page.get_by_label("Top text").fill("ANIMATED MEME")
    with page.expect_response("**/memes") as response_info:
        page.get_by_role("button", name="Create meme").click()
    assert response_info.value.headers["content-type"] == "image/gif"
    expect(page.get_by_role("img", name="Meme")).to_be_visible()
    expect(page.get_by_role("img", name="Meme")).to_have_js_property("naturalWidth", 400)
