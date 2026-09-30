"""Image proxy endpoint: SSRF guard + image-only streaming.

FastAPI's Form/File routes need python-multipart to import the app; skip where
it is absent (the known test-image gap) rather than failing collection.
"""

from contextlib import asynccontextmanager

import pytest

pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

import assistant.backend.main as main  # noqa: E402

app = main.app


class _FakeResponse:
    def __init__(self, content_type: str, chunks: list[bytes]):
        self.headers = {"content-type": content_type}
        self._chunks = chunks

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


async def _public(url: str, **kwargs) -> None:
    return None


def test_blocks_private_urls_without_fetching():
    client = TestClient(app)
    resp = client.get("/image-proxy", params={"url": "http://127.0.0.1/secret.png"})
    assert resp.status_code == 400


def test_rejects_non_image_content_type(monkeypatch):
    @asynccontextmanager
    async def fake_stream(client, url, headers=None, **kwargs):
        yield _FakeResponse("text/html", [b"<html>"])

    monkeypatch.setattr(main, "assert_public_url", _public)
    monkeypatch.setattr(main, "safe_stream", fake_stream)

    client = TestClient(app)
    resp = client.get("/image-proxy", params={"url": "https://example.com/x"})
    assert resp.status_code == 415


def test_rejects_an_oversized_image_instead_of_truncating_it(monkeypatch):
    @asynccontextmanager
    async def fake_stream(client, url, headers=None, **kwargs):
        yield _FakeResponse("image/png", [b"12345", b"6789"])

    monkeypatch.setattr(main, "assert_public_url", _public)
    monkeypatch.setattr(main, "safe_stream", fake_stream)
    monkeypatch.setattr(main, "_MAX_IMAGE_BYTES", 8)

    client = TestClient(app)
    resp = client.get("/image-proxy", params={"url": "https://example.com/big.png"})
    assert resp.status_code == 413


def test_streams_an_image(monkeypatch):
    @asynccontextmanager
    async def fake_stream(client, url, headers=None, **kwargs):
        yield _FakeResponse("image/png", [b"\x89PNG", b"rest"])

    monkeypatch.setattr(main, "assert_public_url", _public)
    monkeypatch.setattr(main, "safe_stream", fake_stream)

    client = TestClient(app)
    resp = client.get("/image-proxy", params={"url": "https://example.com/x.png"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content == b"\x89PNGrest"
