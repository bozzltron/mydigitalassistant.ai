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


async def test_brave_parses_object_thumbnail_and_videos_section():
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
