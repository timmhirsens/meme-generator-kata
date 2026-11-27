"""Error contracts exercised through a real HTTP server."""

import httpx
import pytest


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "status"),
    [
        ("POST", "/memes", {"data": {"top_text": "hello"}}, 400),
        ("GET", "/missing", {}, 404),
        ("PUT", "/health", {}, 405),
        ("POST", "/memes", {"json": {"top_text": "hello"}}, 400),
        (
            "POST",
            "/memes",
            {"content": b"broken", "headers": {"Content-Type": "multipart/form-data"}},
            400,
        ),
    ],
)
def test_errors_have_one_schema(
    client: httpx.Client, method: str, path: str, kwargs: dict[str, object], status: int
) -> None:
    response = client.request(method, path, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == status
    assert set(response.json()) == {"error", "message"}
    assert all(isinstance(value, str) and value for value in response.json().values())
