import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from meme_generator.captions import CaptionError
from meme_generator.logs import RequestLoggingMiddleware, request_id
from meme_generator.rendering import load_font
from meme_generator.service import create_meme as process_meme
from meme_generator.uploads import UploadError, read_upload

INDEX_HTML = Path(__file__).with_name("index.html")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": code, "message": message, "request_id": request_id.get()}, status_code=status
    )


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
    app.add_middleware(RequestLoggingMiddleware)
    # Admit before consuming the upload, not after buffering or decoding it.
    # Three in-flight uploads/renders; the pipeline separately budgets the total
    # full-resolution pixels so concurrency cannot multiply the 25 MP decode budget.
    slots = asyncio.Semaphore(3)

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

    @app.exception_handler(UploadError)
    async def upload_error(_: Request, exc: UploadError) -> JSONResponse:
        response = _error(exc.status, exc.code, str(exc))
        if exc.status == 503:
            response.headers["Retry-After"] = "1"
        return response

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
                "content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}, "image/gif": {}},
                "description": "The meme in the uploaded image's format",
            }
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["image"],
                            "properties": {
                                "image": {"type": "string", "format": "binary"},
                                "top_text": {"type": "string"},
                                "bottom_text": {"type": "string"},
                            },
                        }
                    }
                },
            }
        },
    )
    async def create_meme(request: Request) -> Response:
        metrics = request.state.metrics
        started = perf_counter()
        try:
            await asyncio.wait_for(slots.acquire(), timeout=3)
        except TimeoutError:
            response = _error(503, "busy", "The service is busy. Please retry shortly.")
            response.headers["Retry-After"] = "1"
            return response
        try:
            metrics["queue_ms"] = round((perf_counter() - started) * 1000, 3)
            started = perf_counter()
            image, top, bottom = await asyncio.wait_for(read_upload(request), timeout=10)
            metrics["upload_ms"] = round((perf_counter() - started) * 1000, 3)
            data, media_type = await run_in_threadpool(process_meme, image, top, bottom, metrics)
            return Response(data, media_type=media_type)
        except TimeoutError:
            return _error(408, "upload_timeout", "The upload took too long.")
        finally:
            slots.release()

    return app


app = create_app()
