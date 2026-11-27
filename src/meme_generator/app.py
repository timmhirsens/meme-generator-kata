import io
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image, UnidentifiedImageError
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartParser

from meme_generator.captions import CaptionError
from meme_generator.limits import MULTIPART_SPOOL_BYTES
from meme_generator.rendering import load_font, render_caption

MultiPartParser.spool_max_size = MULTIPART_SPOOL_BYTES
INDEX_HTML = Path(__file__).with_name("index.html")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    load_font()
    yield


def create_app() -> FastAPI:
    docs = os.environ.get("MEME_DOCS_ENABLED", "true").lower() != "false"
    app = FastAPI(
        title="Meme Generator",
        lifespan=_lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        missing_image = any(error["loc"] == ("body", "image") for error in exc.errors())
        return _error(
            400,
            "image_missing" if missing_image else "invalid_request",
            "Provide an image file." if missing_image else "The request fields are invalid.",
        )

    @app.exception_handler(CaptionError)
    async def caption_error(_: Request, exc: CaptionError) -> JSONResponse:
        return _error(exc.status, exc.code, str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        response = _error(exc.status_code, "http_error", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:
        return _error(500, "internal_error", "The request could not be completed.")

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/memes",
        response_model=None,
        responses={
            200: {
                "content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}},
                "description": "The meme in the uploaded image's format",
            }
        },
    )
    def create_meme(
        image: Annotated[UploadFile, File(...)],
        top_text: Annotated[str | None, Form()] = None,
        bottom_text: Annotated[str | None, Form()] = None,
    ) -> Response:
        if not (top_text or "").strip() and not (bottom_text or "").strip():
            return _error(400, "caption_missing", "Provide top_text, bottom_text or both.")
        try:
            source = Image.open(io.BytesIO(image.file.read()))
            source.load()
        except (UnidentifiedImageError, OSError):
            return _error(415, "unsupported_image", "The image could not be read.")
        format = source.format
        if format not in {"JPEG", "PNG", "WEBP"}:
            return _error(415, "unsupported_image", "Only JPEG, PNG and WebP are supported.")
        mode = "RGB" if format == "JPEG" else "RGBA"
        meme = render_caption(source.convert(mode), top_text, bottom_text)
        out = io.BytesIO()
        meme.save(out, format=format, quality=90)
        return Response(out.getvalue(), media_type=f"image/{format.lower()}")

    return app


app = create_app()
