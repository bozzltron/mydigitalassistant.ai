"""Image/video extraction from search responses.

Locks the shapes that were silently dropped:
- Brave returns ``thumbnail`` as an object ``{src, original}``; the old code
  stored the dict on a ``str`` field and the frontend skipped it.
- Brave's ``videos`` section is parsed from the same web response (no second
  request).
- SearXNG image results resolve relative (image_proxy) ``img_src`` URLs.
"""

import httpx

from assistant.backend.pipeline.search import (
    BraveBackend,
    SearXNGBackend,
    _absolute,
    _thumb_urls,
)


class TestThumbUrls:
    def test_string_thumbnail(self):
        assert _thumb_urls({"thumbnail": "https://x/y.png"}) == (
            "https://x/y.png",
            "https://x/y.png",
        )

    def test_object_thumbnail_prefers_src_for_preview(self):
        assert _thumb_urls({"thumbnail": {"src": "s", "original": "o"}}) == ("s", "o")

    def test_object_thumbnail_falls_back_to_original(self):
        assert _thumb_urls({"thumbnail": {"original": "o"}}) == ("o", "o")

    def test_image_fallback(self):
        assert _thumb_urls({"image": {"src": "s", "original": "o"}}) == ("s", "o")

    def test_missing(self):
        assert _thumb_urls({}) == (None, None)


class TestAbsolute:
    def test_absolute_unchanged(self):
        assert _absolute("https://a/b.png", "http://searx:8080") == "https://a/b.png"

    def test_relative_resolved_against_base(self):
        assert _absolute("/image_proxy?url=x", "http://searx:8080") == (
            "http://searx:8080/image_proxy?url=x"
        )


def _brave_backend(handler) -> BraveBackend:
    backend = BraveBackend(api_key="test")
    backend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0
    )
    return backend


async def test_brave_parses_object_thumbnail_and_videos_section(monkeypatch):
    from assistant.backend.config import settings

    # /web/search is the fallback now (LLM Context is the default); pin the flag
    # off so this exercises the web path.
    monkeypatch.setattr(settings, "brave_llm_context", False)

    payload = {
        "web": {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.example/1",
                    "description": "d",
                    "thumbnail": {
                        "src": "https://img/a-s.png",
                        "original": "https://img/a-o.png",
                    },
                },
                {"title": "B", "url": "https://b.example/2", "description": "d"},
            ]
        },
        "videos": {
            "results": [
                {
                    "title": "V",
                    "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "thumbnail": {"src": "https://img/v.png", "original": "https://img/v-o.png"},
                    "video": {"creator": "Chan"},
                }
            ]
        },
    }

    async def handler(request):
        return httpx.Response(200, json=payload)

    backend = _brave_backend(handler)
    try:
        results, videos = await backend.search("q", num_results=5)
    finally:
        await backend.close()

    assert results[0].thumbnail == "https://img/a-s.png"
    assert results[0].image == "https://img/a-o.png"
    assert results[1].thumbnail is None
    assert len(videos) == 1
    assert videos[0].video_id == "abcdefghijk"
    assert videos[0].thumbnail_url == "https://img/v.png"
    assert videos[0].channel_title == "Chan"


async def test_brave_requests_and_parses_extra_snippets(monkeypatch):
    """`extra_snippets=true` is sent and the extra excerpts are captured.

    This is the `/web/search` fallback path (LLM Context is the default); the
    extras enrich the extraction input (docs/SEARCH.md §5) and are kept off
    `snippet` so the prompt and the relevance gate stay lean.
    """
    from assistant.backend.config import settings

    monkeypatch.setattr(settings, "brave_llm_context", False)

    payload = {
        "web": {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.example/1",
                    "description": "main snippet",
                    "extra_snippets": ["extra one", "extra two"],
                },
                {"title": "B", "url": "https://b.example/2", "description": "d"},
            ]
        }
    }
    seen: dict = {}

    async def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=payload)

    backend = _brave_backend(handler)
    try:
        results, _ = await backend.search("q", num_results=5)
    finally:
        await backend.close()

    assert seen["params"].get("extra_snippets") == "true"
    assert results[0].snippet == "main snippet"
    assert results[0].extra_snippets == ["extra one", "extra two"]
    assert results[1].extra_snippets == []


async def test_brave_llm_context_parses_grounding_and_sources(monkeypatch):
    """`BRAVE_LLM_CONTEXT` uses /llm/context: chunks become snippet + extras.

    Phase 2 (dev). Videos are empty (the endpoint has none) and images come from
    ``sources[].thumbnail`` rather than the image index. See docs/SEARCH.md.
    """
    from assistant.backend.config import settings

    monkeypatch.setattr(settings, "brave_llm_context", True)
    payload = {
        "grounding": {
            "generic": [
                {
                    "url": "https://a.example/1",
                    "title": "A",
                    "snippets": ["first chunk", "second chunk"],
                },
                {"url": "https://b.example/2", "title": "B", "snippets": ["b chunk"]},
                {"url": "https://c.example/3", "title": "C", "snippets": []},
            ]
        },
        "sources": {
            "https://a.example/1": {
                "title": "A",
                "thumbnail": {
                    "src": "https://img/s.png",
                    "original": "https://img/o.png",
                },
                "age": [
                    "Monday, November 10, 2025",
                    "2025-11-10",
                    "336 days ago",
                    "2025-11-10T19:16:26Z",
                ],
            }
        },
    }
    seen: dict = {}

    async def handler(request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=payload)

    backend = _brave_backend(handler)
    try:
        results, videos = await backend.search("q", num_results=5)
    finally:
        await backend.close()

    assert videos == []
    # The URL with no chunks is dropped.
    assert [r.url for r in results] == ["https://a.example/1", "https://b.example/2"]
    assert results[0].snippet == "first chunk"
    assert results[0].extra_snippets == ["second chunk"]
    assert results[0].thumbnail == "https://img/s.png"
    assert results[0].image == "https://img/o.png"
    # The page date rides on the result (freshness signal for prompt + extraction).
    assert results[0].published == "2025-11-10"
    assert results[1].snippet == "b chunk"
    assert results[1].published is None  # no age in sources → unknown
    assert seen["path"].endswith("/llm/context")
    assert seen["params"].get("enable_source_metadata") == "true"


def test_normalize_url_collapses_duplicate_slashes():
    """Brave's index returns URLs like `.../3.11//whatsnew/...`; dedup must see them."""
    from assistant.backend.pipeline.search import normalize_url

    assert normalize_url("https://docs.python.org/3.11//whatsnew/3.11.html") == (
        "https://docs.python.org/3.11/whatsnew/3.11.html"
    )
    # trailing slash, fragment and tracking param still collapse (host lowercased).
    assert normalize_url("https://Example.com/A//b/?utm_source=x#frag") == (
        "https://example.com/A/b"
    )


async def test_searxng_image_result_resolves_relative_img_src():
    payload = {
        "results": [
            {
                "title": "Pic",
                "url": "https://page.example/p",
                "content": "c",
                "engine": "bing",
                "img_src": "/image_proxy?url=https%3A%2F%2Fx.example%2Fp.jpg",
                "thumbnail_src": "/image_proxy?url=t",
            }
        ]
    }

    async def handler(request):
        return httpx.Response(200, json=payload)

    backend = SearXNGBackend(base_url="http://searx.test")
    backend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0
    )
    try:
        results, _ = await backend.search("q", num_results=5)
    finally:
        await backend.close()

    assert results[0].image == (
        "http://searx.test/image_proxy?url=https%3A%2F%2Fx.example%2Fp.jpg"
    )
    assert results[0].thumbnail == "http://searx.test/image_proxy?url=t"
