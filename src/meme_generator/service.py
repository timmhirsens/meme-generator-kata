"""The image pipeline shared by the HTTP and local-file interfaces."""

import io
from time import perf_counter

from PIL import Image, UnidentifiedImageError

from meme_generator.limits import MAX_LONG_SIDE, MAX_SOURCE_PIXELS, MAX_UPLOAD_BYTES
from meme_generator.rendering import render_caption
from meme_generator.uploads import UploadError


def create_meme(
    image: bytes, top_text: str, bottom_text: str, metrics: dict[str, object] | None = None
) -> tuple[bytes, str]:
    if metrics is None:
        metrics = {}
    metrics.update(top_length=len(top_text), bottom_length=len(bottom_text))
    if len(image) > MAX_UPLOAD_BYTES:
        raise UploadError(413, "upload_too_large", "The image exceeds 10 MiB.")
    top, bottom = top_text, bottom_text
    if not top.strip() and not bottom.strip():
        raise UploadError(400, "caption_missing", "Provide top_text, bottom_text or both.")
    started = perf_counter()
    try:
        source = Image.open(io.BytesIO(image))
        metrics.update(format=source.format, width=source.width, height=source.height)
        if source.width * source.height > MAX_SOURCE_PIXELS:
            raise UploadError(422, "image_too_large", "The image exceeds 25 megapixels.")
        format = source.format
        if format not in {"JPEG", "PNG", "WEBP"}:
            raise UploadError(415, "unsupported_image", "Only JPEG, PNG and WebP are supported.")
        source.draft("RGB", (MAX_LONG_SIDE, MAX_LONG_SIDE))
        source.load()
    except Image.DecompressionBombError as exc:
        raise UploadError(422, "image_too_large", "The image has too many pixels.") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise UploadError(415, "unsupported_image", "The image could not be read.") from exc
    metrics["decode_ms"] = round((perf_counter() - started) * 1000, 3)
    started = perf_counter()
    source.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE), Image.Resampling.LANCZOS)
    mode = "RGB" if format == "JPEG" else "RGBA"
    meme = render_caption(source.convert(mode), top, bottom)
    metrics["render_ms"] = round((perf_counter() - started) * 1000, 3)
    started = perf_counter()
    out = io.BytesIO()
    meme.save(out, format=format, quality=90)
    metrics["encode_ms"] = round((perf_counter() - started) * 1000, 3)
    return out.getvalue(), f"image/{format.lower()}"
