"""Bounded streaming multipart parsing with no filesystem-backed objects."""

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from meme_generator.limits import MAX_UPLOAD_BYTES

# Space for two UTF-8 captions, multipart headers and boundaries, not a second image.
MAX_FORM_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
MAX_FIELD_BYTES = 4096


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(message)


class _Form:
    def __init__(self) -> None:
        self.fields: dict[str, bytes] = {}
        self.name = b""
        self.header = bytearray()
        self.value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.part = bytearray()
        self.finished = False

    def part_begin(self) -> None:
        self.headers = {}
        self.part = bytearray()

    def header_field(self, data: bytes, start: int, end: int) -> None:
        self.header.extend(data[start:end])

    def header_value(self, data: bytes, start: int, end: int) -> None:
        self.value.extend(data[start:end])

    def header_end(self) -> None:
        self.headers[bytes(self.header).lower()] = bytes(self.value)
        self.header.clear()
        self.value.clear()

    def headers_finished(self) -> None:
        _, options = parse_options_header(self.headers.get(b"content-disposition", b""))
        self.name = options.get(b"name", b"")
        is_file = b"filename" in options
        if self.name not in (b"image", b"top_text", b"bottom_text"):
            raise UploadError(400, "invalid_request", "Unexpected multipart field.")
        if self.name.decode() in self.fields:
            raise UploadError(400, "invalid_request", "Duplicate multipart field.")
        if is_file != (self.name == b"image"):
            raise UploadError(400, "invalid_request", "Provide exactly one image file.")

    def part_data(self, data: bytes, start: int, end: int) -> None:
        limit = MAX_UPLOAD_BYTES if self.name == b"image" else MAX_FIELD_BYTES
        if len(self.part) + end - start > limit:
            if self.name == b"image":
                raise UploadError(413, "upload_too_large", "The image exceeds 10 MiB.")
            raise UploadError(400, "invalid_request", "The caption field is too large.")
        self.part.extend(data[start:end])

    def part_end(self) -> None:
        self.fields[self.name.decode()] = bytes(self.part)
        self.part.clear()

    def end(self) -> None:
        self.finished = True


async def read_upload(request: Request) -> tuple[bytes, str, str]:
    media, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary", b"")
    if media != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise UploadError(400, "invalid_request", "Provide a multipart form with an image file.")
    form = _Form()
    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": form.part_begin,
            "on_header_field": form.header_field,
            "on_header_value": form.header_value,
            "on_header_end": form.header_end,
            "on_headers_finished": form.headers_finished,
            "on_part_data": form.part_data,
            "on_part_end": form.part_end,
            "on_end": form.end,
        },
    )
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_FORM_BYTES:
                raise UploadError(413, "upload_too_large", "The multipart form exceeds its limit.")
            parser.write(chunk)
        parser.finalize()
    except MultipartParseError as exc:
        raise UploadError(400, "invalid_request", "The multipart form is malformed.") from exc
    if not form.finished:
        raise UploadError(400, "invalid_request", "The multipart form is incomplete.")
    image = form.fields.get("image")
    if image is None:
        raise UploadError(400, "image_missing", "Provide an image file.")
    try:
        top = form.fields.get("top_text", b"").decode("utf-8")
        bottom = form.fields.get("bottom_text", b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError(400, "invalid_request", "Captions must use UTF-8.") from exc
    return image, top, bottom
