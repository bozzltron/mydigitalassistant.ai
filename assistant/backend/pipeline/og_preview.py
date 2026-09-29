"""Open Graph link preview fetcher.

Fetches og:image, og:title, og:description, og:site_name meta tags
from source URLs and returns them as a PreviewCard. Results are cached
in-memory with a 1-hour TTL.

Privacy: This module makes outbound HTTP requests to cited domains to fetch
their HTML meta tags. Only metadata is fetched — images load directly in the
browser. This is the standard link-preview behaviour (WhatsApp/Slack/Telegram).
"""

import logging
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from assistant.backend.pipeline.url_safety import UnsafeURLError, assert_public_url, safe_stream

logger = logging.getLogger(__name__)

# Cache: url -> (PreviewCard, fetch_timestamp)
_cache: dict[str, tuple["PreviewCard | None", float]] = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour
_MAX_RESPONSE_SIZE = 2 * 1024 * 1024  # 2 MB max HTML
_FETCH_TIMEOUT = 5.0  # seconds


@dataclass
class PreviewCard:
    """A link preview card with OG metadata."""
    url: str
    title: str | None = None
    description: str | None = None
    image: str | None = None
    site_name: str | None = None


class _OGParser(HTMLParser):
    """Minimal HTML parser to extract Open Graph meta tags."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.description: str | None = None
        self.image: str | None = None
        self.site_name: str | None = None
        self._in_head = False
        self._in_body = False
        self._title_tag_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: v for k, v in attrs}
        if tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_body = True
        elif tag == "title":
            self._title_tag_depth += 1
        elif tag == "meta":
            prop = attrs_dict.get("property", "") or attrs_dict.get("name", "")
            content = attrs_dict.get("content", "")
            if prop == "og:title":
                self.title = content
            elif prop == "og:description":
                self.description = content
            elif prop == "og:image":
                self.image = content
            elif prop == "og:site_name":
                self.site_name = content
            elif prop == "description" and not self.description:
                self.description = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False
        elif tag == "body":
            self._in_body = False
        elif tag == "title":
            self._title_tag_depth = max(0, self._title_tag_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._title_tag_depth > 0 and not self.title:
            self.title = data.strip()


async def fetch_og_preview(url: str) -> PreviewCard | None:
    """Fetch Open Graph metadata for a URL.

    Fetches the HTML (up to 2 MB), parses og:* meta tags, and returns a
    PreviewCard. Results are cached for 1 hour.

    Returns None if the fetch fails, times out, or the URL is blocked
    (private/.internal hosts).
    """
    global _cache

    # Check cache
    now = time.monotonic()
    if url in _cache:
        card, ts = _cache[url]
        if now - ts < _CACHE_TTL_SECONDS:
            return card

    # Validate URL
    try:
        parsed = urlparse(url)
    except Exception:
        logger.warning("Invalid URL: %s", url)
        _cache[url] = (None, now)
        return None

    scheme = parsed.scheme.lower()

    if scheme not in ("http", "https"):
        logger.warning("Blocked non-http(s) URL: %s", url)
        _cache[url] = (None, now)
        return None

    # Resolve and reject private/loopback/link-local/reserved, then re-check on
    # every redirect hop inside safe_stream(). A hostname string check cannot see
    # a name that resolves to 127.0.0.1 or a public URL that redirects inward.
    try:
        await assert_public_url(url)
    except UnsafeURLError as e:
        logger.warning("Blocked URL %s: %s", url, e)
        _cache[url] = (None, now)
        return None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_FETCH_TIMEOUT, connect=3.0),
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LinkPreview/1.0)"},
        ) as client:
            async with safe_stream(
                client, url, headers={"Accept": "text/html"}
            ) as response:
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type.lower():
                    logger.debug("Non-HTML content-type %s for %s", content_type, url)
                    _cache[url] = (None, now)
                    return None

                # Read at most _MAX_RESPONSE_SIZE bytes off the wire; the old
                # `response.text[:N]` had already buffered the whole body.
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _MAX_RESPONSE_SIZE:
                        break
                raw = b"".join(chunks)
                encoding = response.encoding or "utf-8"

        html = raw[:_MAX_RESPONSE_SIZE].decode(encoding, errors="replace")
        parser = _OGParser()
        parser.feed(html)

        card = PreviewCard(
            url=url,
            title=parser.title or parser.description,
            description=parser.description,
            image=parser.image,
            site_name=parser.site_name,
        )
        _cache[url] = (card, now)
        return card

    except httpx.TimeoutException:
        logger.debug("Timeout fetching OG preview: %s", url)
        _cache[url] = (None, now)
        return None
    except Exception as e:
        logger.debug("OG preview fetch failed for %s: %s", url, e)
        _cache[url] = (None, now)
        return None
