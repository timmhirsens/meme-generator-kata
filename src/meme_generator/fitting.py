"""Word wrapping and fitting for a single, independent caption block."""

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from meme_generator.captions import FONT_PATH, CaptionError


def initial_font_size(image_height: int) -> int:
    return max(14, image_height // 10)


def stroke_width(font_size: int) -> int:
    return max(1, font_size // 15)


@lru_cache(maxsize=128)
def font_at_size(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def fit_caption(text: str, width: int, height: int) -> tuple[str, ImageFont.FreeTypeFont]:
    """Fit uppercase text into 90% width and 25% height, or reject without truncation."""
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    minimum = max(14, height // 30)
    for size in range(initial_font_size(height), minimum - 1, -1):
        font = font_at_size(size)
        outline = stroke_width(size)
        lines: list[str] = []
        for paragraph in text.split("\n"):
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}" if line else word
                left, _, right, _ = font.getbbox(candidate, stroke_width=outline)
                if line and right - left > width * 0.9:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        wrapped = "\n".join(lines)
        left, top, right, bottom = draw.multiline_textbbox(
            (0, 0), wrapped, font=font, align="center", stroke_width=outline
        )
        if right - left <= width * 0.9 and bottom - top <= height * 0.25:
            return wrapped, font
    raise CaptionError(
        "caption_does_not_fit",
        "The caption does not fit the image, even at the minimum font size. "
        "Use fewer words, shorter words or a larger image.",
    )
