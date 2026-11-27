"""Rendering: draw the Caption on one frame."""

from PIL import Image, ImageDraw

from meme_generator.captions import prepare_caption, supported_codepoints
from meme_generator.fitting import fit_caption, font_at_size, initial_font_size, stroke_width

FILL = "white"
OUTLINE = "black"


def load_font() -> None:
    """Fail at startup, not on the first request, if the font is missing."""
    font_at_size(initial_font_size(1000))
    supported_codepoints()


def render_caption(
    frame: Image.Image, top_text: str | None, bottom_text: str | None
) -> Image.Image:
    """Return a copy of `frame` with the Caption drawn in Meme style."""
    top_text = prepare_caption(top_text)
    bottom_text = prepare_caption(bottom_text)
    meme = frame.copy()
    width, height = meme.size
    margin = max(4, height // 40)
    draw = ImageDraw.Draw(meme)

    for text, at_bottom in [(top_text, False), (bottom_text, True)]:
        if not text:
            continue
        wrapped, font = fit_caption(text, width, height)
        outline = stroke_width(int(font.size))
        left, top, right, bottom = draw.multiline_textbbox(
            (0, 0), wrapped, font=font, align="center", stroke_width=outline
        )
        y = height - margin - bottom if at_bottom else margin - top
        draw.multiline_text(
            (width / 2 - (left + right) / 2, y),
            wrapped,
            align="center",
            font=font,
            fill=FILL,
            stroke_width=outline,
            stroke_fill=OUTLINE,
        )

    return meme
