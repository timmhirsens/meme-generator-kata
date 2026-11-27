"""The image pipeline shared by the HTTP and local-file interfaces."""

import io
from time import perf_counter

from PIL import GifImagePlugin, Image, ImageSequence, UnidentifiedImageError

from meme_generator.limits import MAX_LONG_SIDE, MAX_SOURCE_PIXELS, MAX_UPLOAD_BYTES
from meme_generator.rendering import render_caption
from meme_generator.uploads import UploadError


def _animated_meme(source: Image.Image, top: str, bottom: str) -> bytes:
    if getattr(source, "n_frames", 1) > 300:
        raise UploadError(422, "too_many_frames", "GIF images may contain at most 300 frames.")
    out = io.BytesIO()
    for index, frame in enumerate(ImageSequence.Iterator(source)):
        if frame.width * frame.height > MAX_SOURCE_PIXELS:
            raise UploadError(422, "image_too_large", "The image exceeds 25 megapixels.")
        duration = frame.info.get("duration", 0)
        rgba = frame.convert("RGBA")
        rgba.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE), Image.Resampling.LANCZOS)
        meme = render_caption(rgba, top, bottom)
        # Full composited frames, independently paletted, avoid retaining hundreds
        # of decoded frames. Index 255 is reserved for transparent pixels.
        paletted = meme.convert("RGB").quantize(colors=255)
        # Pillow's point annotation contains an unresolved internal type.
        transparent = meme.getchannel("A").point(  # pyright: ignore[reportUnknownMemberType]
            [255] * 128 + [0] * 128
        )
        paletted.paste(255, mask=transparent)
        palette = paletted.getpalette() or []
        paletted.putpalette((palette + [0] * 768)[:768])
        if index == 0:
            info: dict[str, object] = {"transparency": 255, "background": 255}
            if "loop" in source.info:
                info["loop"] = source.info["loop"]
            headers, _ = GifImagePlugin.getheader(paletted, info=info)
            for chunk in headers:
                out.write(chunk)
        for chunk in GifImagePlugin.getdata(
            paletted, duration=duration, disposal=2, transparency=255, include_color_table=True
        ):
            out.write(chunk)
    out.write(b";")
    return out.getvalue()


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
        if source.format == "GIF":
            result = _animated_meme(source, top, bottom)
            metrics["animation_ms"] = round((perf_counter() - started) * 1000, 3)
            return result, "image/gif"
        format = source.format
        if format not in {"JPEG", "PNG", "WEBP"}:
            raise UploadError(
                415, "unsupported_image", "Only JPEG, PNG, WebP and GIF are supported."
            )
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
