"""Regression: outbound fetchers must not reach private/internal addresses.

`og_preview` and the `fetch_url` tool both make server-side requests to
user/model-supplied URLs. Their old guard matched the *hostname string* against
a short regex list, which is not a security boundary:

- it cannot see a name that resolves to `127.0.0.1`,
- it missed `169.254.0.0/16` (link-local — the cloud-metadata address),
  `100.64.0.0/10`, IPv6 `fc00::/7`, and IPv4-mapped IPv6,
- it was applied once and never again, so `follow_redirects=True` could bounce a
  public URL into the private network.

The tests below pin the shared `url_safety` checks and the callers' use of them.
"""

from __future__ import annotations

import httpx
import pytest

import assistant.backend.pipeline.og_preview as og_preview
import assistant.backend.pipeline.tools as tools
from assistant.backend.pipeline.url_safety import (
    UnsafeURLError,
    assert_public_url,
    safe_stream,
)


class _NoNetworkClient:
    """Fails the test if any request is attempted."""

    attempts: list[str] = []

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        type(self).attempts.append("constructed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):  # noqa: ANN002
        return False

    async def get(self, url, **kwargs):  # noqa: ANN001, ANN003
        type(self).attempts.append(url)
        raise RuntimeError("network disabled in test")


@pytest.fixture(autouse=True)
def _reset():
    _NoNetworkClient.attempts = []
    og_preview._cache.clear()
    yield
    _NoNetworkClient.attempts = []
    og_preview._cache.clear()


# --- the validator itself -----------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",  # cloud metadata / link-local: the old gap
        "100.64.0.1",  # carrier-grade NAT: the old gap
        "0.0.0.0",
        "[::1]",
        "[fc00::1]",  # IPv6 unique-local: the old gap
        "[::ffff:127.0.0.1]",  # IPv4-mapped IPv6
    ],
)
@pytest.mark.asyncio
async def test_private_addresses_are_rejected(host: str):
    with pytest.raises(UnsafeURLError):
        await assert_public_url(f"http://{host}/path")


@pytest.mark.asyncio
async def test_public_literal_ip_is_allowed():
    # A literal public address needs no DNS and must pass.
    await assert_public_url("http://93.184.216.34/")


@pytest.mark.asyncio
async def test_localhost_resolving_to_loopback_is_rejected():
    with pytest.raises(UnsafeURLError):
        await assert_public_url("http://localhost:8080/")


@pytest.mark.asyncio
async def test_non_http_scheme_is_rejected():
    with pytest.raises(UnsafeURLError):
        await assert_public_url("file:///etc/passwd")


# --- redirect hops are revalidated -------------------------------------------


@pytest.mark.asyncio
async def test_redirect_to_a_private_host_is_refused():
    followed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        followed.append(str(request.url))
        if request.url.host == "93.184.216.34":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        return httpx.Response(200, text="should never be read")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(UnsafeURLError):
            async with safe_stream(client, "http://93.184.216.34/start"):
                pass

    # Only the public hop was ever requested.
    assert followed == ["http://93.184.216.34/start"]


@pytest.mark.asyncio
async def test_safe_stream_returns_a_public_body():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>ok</html>"))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        async with safe_stream(client, "http://93.184.216.34/") as response:
            body = b"".join([chunk async for chunk in response.aiter_bytes()])
    assert b"ok" in body


# --- callers refuse before any request ---------------------------------------


@pytest.mark.asyncio
async def test_og_preview_refuses_link_local_without_a_request(monkeypatch):
    monkeypatch.setattr(og_preview.httpx, "AsyncClient", _NoNetworkClient)

    card = await og_preview.fetch_og_preview("http://169.254.169.254/latest/meta-data/")

    assert card is None
    assert _NoNetworkClient.attempts == []


@pytest.mark.asyncio
async def test_fetch_url_refuses_link_local_without_a_request(monkeypatch):
    monkeypatch.setattr(tools.httpx, "AsyncClient", _NoNetworkClient)

    result = await tools._fetch_single_url("http://169.254.169.254/")

    assert result.startswith("Error:")
    assert _NoNetworkClient.attempts == []


# --- the byte cap is real -----------------------------------------------------


@pytest.mark.asyncio
async def test_read_capped_stops_early():
    class _Counter:
        def __init__(self) -> None:
            self.chunks = 0

        async def aiter_bytes(self):
            for _ in range(100):
                self.chunks += 1
                yield b"x" * 1000

    counter = _Counter()
    data = await tools._read_capped(counter, 2500)  # type: ignore[arg-type]

    assert len(data) == 2500
    # Read three chunks, not the 100 the "server" offered.
    assert counter.chunks == 3
