"""SSRF-safe outbound URL validation, shared by `og_preview` and `fetch_url`.

Both modules make server-side requests to URLs supplied by the user or the
model, and both used to rely on a hostname *string* check. That check cannot see
through DNS (a name that resolves to `127.0.0.1`), misses whole ranges
(`169.254.0.0/16` link-local — the cloud-metadata address — `100.64.0.0/10`,
IPv6 `fc00::/7`, integer/hex forms), and is not re-applied to redirect targets.

This module resolves the host and validates every address it maps to, and
`assert_public_url` is re-run on each redirect hop so a public URL cannot bounce
the request into the private network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# Ranges that `ipaddress` does not classify as private on every supported
# Python (carrier-grade NAT, IPv6 unique-local in older patch levels).
_EXTRA_BLOCKED = [
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_MAX_REDIRECTS = 5


class UnsafeURLError(Exception):
    """Raised when a URL is not http(s) or resolves to a non-public address."""


def _normalize(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """Unwrap IPv4-mapped IPv6 so `::ffff:127.0.0.1` is seen as loopback."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = _normalize(ip)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    # `is_private` is version-dependent for these; be explicit.
    try:
        if any(ip in net for net in _EXTRA_BLOCKED):
            return False
    except TypeError:
        # Mixed-family membership raises; the address is public for our net.
        pass
    return True


async def _resolve(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURLError(f"cannot resolve host {hostname!r}: {e}") from e
    addresses = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not addresses:
        raise UnsafeURLError(f"host {hostname!r} resolved to no usable address")
    return addresses


def _origin(url: str) -> tuple[str, str | None, int | None] | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return (parsed.scheme.lower(), parsed.hostname, parsed.port or default_port)


def url_is_trusted(url: str, trusted_prefixes: list[str] | None) -> bool:
    """True when `url` shares an origin with one of the trusted base URLs.

    Lets the backend reach its own SearXNG instance (an internal host) through
    the SSRF guard without opening the guard to arbitrary private addresses.
    """
    if not trusted_prefixes:
        return False
    target = _origin(url)
    if target is None:
        return False
    return any(_origin(prefix) == target for prefix in trusted_prefixes)


async def assert_public_url(
    url: str, *, trusted_prefixes: list[str] | None = None
) -> None:
    """Raise `UnsafeURLError` unless `url` is http(s) and resolves only to public IPs.

    A URL whose origin matches a trusted prefix (e.g. the local SearXNG base) is
    allowed without the public-IP check.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:  # pragma: no cover - urlparse rarely raises
        raise UnsafeURLError(f"malformed URL: {e}") from e

    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeURLError(f"only http(s) URLs are allowed, got {parsed.scheme!r}")

    if url_is_trusted(url, trusted_prefixes):
        return

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL has no host")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if not _ip_is_public(literal):
            raise UnsafeURLError(f"blocked non-public address {hostname}")
        return

    for ip in await _resolve(hostname):
        if not _ip_is_public(ip):
            raise UnsafeURLError(f"{hostname} resolves to non-public address {ip}")


@asynccontextmanager
async def safe_stream(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_redirects: int = _MAX_REDIRECTS,
    trusted_prefixes: list[str] | None = None,
) -> AsyncIterator[httpx.Response]:
    """Open a streaming GET, validating the host before every redirect hop.

    `client` must be created with `follow_redirects=False`; the redirect loop
    lives here so each `Location` is revalidated rather than followed blindly.
    """
    current = url
    for _ in range(max_redirects + 1):
        await assert_public_url(current, trusted_prefixes=trusted_prefixes)
        request = client.build_request("GET", current, headers=headers)
        response = await client.send(request, stream=True)
        if response.is_redirect:
            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise UnsafeURLError("redirect response without a Location header")
            current = urljoin(current, location)
            continue
        try:
            yield response
        finally:
            await response.aclose()
        return
    raise UnsafeURLError(f"too many redirects (>{max_redirects}) starting at {url}")
