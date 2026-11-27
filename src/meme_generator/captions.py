"""Caption rules shared by every rendering caller."""

import unicodedata
from functools import cache
from pathlib import Path

from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]

from meme_generator.limits import MAX_TEXT_CHARS

FONT_PATH = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Anton-Regular.ttf"


class CaptionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@cache
def supported_codepoints() -> frozenset[int]:
    with TTFont(FONT_PATH) as font:
        cmap = font.getBestCmap()
        assert cmap is not None, "Anton must provide a Unicode character map"
        return frozenset(cmap)


def prepare_caption(text: str | None) -> str:
    """Validate the original input before stripping and converting to uppercase."""
    text = text or ""
    if len(text) > MAX_TEXT_CHARS:
        raise CaptionError(
            "caption_too_long", f"Captions may contain at most {MAX_TEXT_CHARS} characters.", 400
        )
    # Browser multipart forms encode textarea newlines as CRLF.
    text = text.replace("\r\n", "\n")
    if any(unicodedata.category(char) in {"Cc", "Cf"} and char != "\n" for char in text):
        raise CaptionError(
            "caption_control", "Captions must not contain control characters except newline.", 400
        )
    unsupported = sorted(
        {
            char
            for char in text + text.upper()
            if char != "\n" and ord(char) not in supported_codepoints()
        }
    )
    if unsupported:
        raise CaptionError(
            "caption_unsupported", "Anton does not support: " + " ".join(unsupported)
        )
    return text.strip().upper()
