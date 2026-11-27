"""Rendering: draw the Caption on one frame."""

from functools import cache

from PIL import Image, ImageDraw, ImageFont

from meme_generator.captions import FONT_PATH, prepare_caption, supported_codepoints
from meme_generator.fitting import initial_font_size, stroke_width

FILL = "white"
OUTLINE = "black"


@cache
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def load_font() -> None:
    """Fail at startup, not on the first request, if the font is missing."""
    _font(initial_font_size(1000))
    supported_codepoints()


def render_caption(
    frame: Image.Image, top_text: str | None, bottom_text: str | None
) -> Image.Image:
    """Return a copy of `frame` with the Caption drawn in Meme style."""
    top_text = prepare_caption(top_text)
    bottom_text = prepare_caption(bottom_text)
    meme = frame.copy()
    width, height = meme.size
    size = initial_font_size(height)
    font = _font(size)
    margin = max(4, height // 40)
    draw = ImageDraw.Draw(meme)
    outline = stroke_width(size)

    def write(xy: tuple[float, float], text: str, anchor: str) -> None:
        draw.multiline_text(
            xy,
            text,
            anchor=anchor,
            align="center",
            font=font,
            fill=FILL,
            stroke_width=outline,
            stroke_fill=OUTLINE,
        )

    if top_text:
        write((width / 2, margin), top_text, "ma")
    if bottom_text:
        write((width / 2, height - margin), bottom_text, "md")
    return meme
