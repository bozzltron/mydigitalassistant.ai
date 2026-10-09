"""Search pipeline hardening — locks result-quality behavior (unit + e2e).

Core functionality under lock:
- sanitize_query strips conversational filler before hitting SearXNG.
- SearXNGBackend pins safesearch/language, ranks by engine score, dedups
  normalized URLs, slices to num_results, and degrades to [] on errors.
- filter_relevant drops results embedding far from the query, and passes
  everything through when the embedder is unavailable (never blinds search).
- Router emits a keyword search_query on searchable turns; orchestrator uses
  it over raw message text and excludes irrelevant links from prompt+citations.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from assistant.backend.pipeline.search import (
    SearchResult,
    SearXNGBackend,
    WebSearchTool,
    filter_relevant,
    normalize_url,
    sanitize_query,
)
from assistant.backend.pipeline.task_router import ClassificationResult


def _backend_with_transport(handler) -> SearXNGBackend:
    """Backend wired to an in-memory transport; no network involved."""
    backend = SearXNGBackend(base_url="http://searx.test")
    backend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0
    )
    return backend


def _searx_payload(results):
    return {"results": results}


class TestSanitizeQuery:
    def test_strips_filler_and_meta_language(self):
        q = sanitize_query("Hey, can you look up the weather in Austin please?")
        assert "hey" not in q.lower()
        assert "can you" not in q.lower()
        assert "please" not in q.lower()
        assert "weather" in q.lower()
        assert "austin" in q.lower()

    def test_caps_token_count(self):
        q = sanitize_query(" ".join(["word"] * 40))
        assert len(q.split()) <= 16

    def test_preserves_plain_queries(self):
        assert sanitize_query("asteroid city plot summary") == \
            "asteroid city plot summary"


class _StubDistillLLM:
    """A minimal chat client that returns a fixed distillation."""

    def __init__(self, content: str):
        self._content = content
        self.utility_model = "stub-utility"

    async def chat(self, messages, **kwargs):
        from assistant.backend.pipeline.llm_client import ChatResponse

        return ChatResponse(content=self._content, model="stub", done=True)


class TestDistillSearchQuery:
    """The query that actually leaves for the engine is distilled, not raw.

    Regression: the fallback was `sanitize_query(request.message)`, so a scheduled
    task's script ("Monitor and alert for Mozilla release dates...") was searched
    verbatim -- the engine looked for the *instruction*, which is how
    "search how to research" results appeared.
    """

    @pytest.mark.asyncio
    async def test_returns_a_sanitized_query(self):
        from assistant.backend.pipeline.search import distill_search_query

        llm = _StubDistillLLM("Mozilla ACL submission deadline November 2026")
        q = await distill_search_query(
            "Monitor and alert for Mozilla release dates ACL submission deadline",
            llm,
        )
        assert q == "Mozilla ACL submission deadline November 2026"

    @pytest.mark.asyncio
    async def test_none_when_the_model_declines(self):
        from assistant.backend.pipeline.search import distill_search_query

        assert await distill_search_query("prices for these items", _StubDistillLLM("NONE")) is None

    @pytest.mark.asyncio
    async def test_none_on_empty_output(self):
        from assistant.backend.pipeline.search import distill_search_query

        assert await distill_search_query("x", _StubDistillLLM("")) is None

    @pytest.mark.asyncio
    async def test_none_when_the_call_fails(self):
        from assistant.backend.pipeline.search import distill_search_query

        class Boom:
            utility_model = "u"

            async def chat(self, *a, **k):
                raise RuntimeError("down")

        assert await distill_search_query("x", Boom()) is None


class TestNormalizeUrl:
    def test_strips_tracking_params(self):
        a = normalize_url("https://example.com/page?utm_source=x&id=2")
        b = normalize_url("https://example.com/page?id=2&utm_campaign=y")
        assert a == b == "https://example.com/page?id=2"

    def test_fragment_and_trailing_slash_ignored(self):
        assert normalize_url("https://example.com/a/#top") == \
            normalize_url("https://EXAMPLE.com/a")

    def test_malformed_url_returned_as_is(self):
        weird = "not-a-url-but-keep-it"
        assert normalize_url(weird) == weird


class TestSearXNGBacking:
    def test_sends_safesearch_language_and_sanitized_query(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=_searx_payload([]))

        backend = _backend_with_transport(handler)
        asyncio.run(backend.search("hey can you look up tesla stock price"))

        assert captured["format"] == "json"
        assert captured["safesearch"] == str(1)
        assert captured["language"] == "en"
        assert "hey" not in captured["q"].lower()

    def test_ranks_by_score_before_slicing(self):
        payload = _searx_payload([
            {"url": "https://a.com", "title": "low", "content": "x", "score": 1},
            {"url": "https://b.com", "title": "high", "content": "x", "score": 9},
            {"url": "https://c.com", "title": "mid", "content": "x", "score": 5},
        ])
        backend = _backend_with_transport(
            lambda req: httpx.Response(200, json=payload)
        )
        results, _ = asyncio.run(backend.search("q", num_results=2))
        assert [r.title for r in results] == ["high", "mid"]

    def test_dedups_tracking_param_variants(self):
        payload = _searx_payload([
            {"url": "https://a.com/p?utm_src=x", "title": "one", "score": 3},
            {"url": "https://a.com/p", "title": "dupe", "score": 2},
        ])
        backend = _backend_with_transport(
            lambda req: httpx.Response(200, json=payload)
        )
        results, _ = asyncio.run(backend.search("q"))
        assert [r.title for r in results] == ["one"]

    def test_non_numeric_scores_do_not_crash(self):
        payload = _searx_payload([
            {"url": "https://a.com", "title": "str-score", "score": "high"},
            {"url": "https://b.com", "title": "none"},
        ])
        backend = _backend_with_transport(
            lambda req: httpx.Response(200, json=payload)
        )
        results, _ = asyncio.run(backend.search("q"))
        assert {r.title for r in results} == {"str-score", "none"}

    def test_http_error_returns_empty_list(self):
        backend = _backend_with_transport(
            lambda req: httpx.Response(503, text="down")
        )
        results, _ = asyncio.run(backend.search("q"))
        assert results == []

    def test_disabled_tool_returns_empty_list(self):
        tool = WebSearchTool(enabled=False)
        results, videos = asyncio.run(tool.search("anything"))
        assert results == []
        assert videos == []


def _fake_embed(vectors_by_prefix):
    """Deterministic embed_fn keyed on first token of the text.

    Now handles both single strings and lists of strings.
    """

    def embed_single(text: str) -> list[float]:
        key = text.split()[0] if text.split() else ""
        vec = vectors_by_prefix.get(key, vectors_by_prefix["_default"])
        return list(vec)

    async def embed(text: str | list[str]) -> list[float] | list[list[float]]:
        if isinstance(text, list):
            return [embed_single(t) for t in text]
        return embed_single(text)

    return embed


class TestFilterRelevant:
    def test_keeps_related_drops_unrelated(self):
        unrelated = [0.0, 1.0]
        query_vec = [1.0, 0.0]
        embed = _fake_embed({
            "weather": query_vec,
            "_default": unrelated,
        })
        # Title's first token drives the fake vector.
        results = [
            SearchResult(title="weather austin today", url="https://good.com",
                         snippet="", engine="t"),
            SearchResult(title="pasta carbonara recipe", url="https://bad.com",
                         snippet="", engine="t"),
        ]
        kept = asyncio.run(filter_relevant(results, "weather", embed, min_relevance=0.5))
        assert [r.url for r in kept] == ["https://good.com"]

    def test_embedder_failure_keeps_all_results(self):
        async def broken(_text):
            raise RuntimeError("ollama down")

        results = [
            SearchResult(title="t1", url="https://a.com", snippet="", engine="t"),
            SearchResult(title="t2", url="https://b.com", snippet="", engine="t"),
        ]
        kept = asyncio.run(filter_relevant(results, "query", broken))
        assert len(kept) == 2

    def test_no_embedder_is_pass_through(self):
        results = [SearchResult(title="t", url="u", snippet="", engine="t")]
        assert asyncio.run(filter_relevant(results, "q", None)) == results


class TestRouterSearchQuery:
    def test_classification_carries_search_query(self):
        result = ClassificationResult.model_validate({
            "task_type": "search", "wants_search": True,
            "search_query": "tesla stock price",
        })
        assert result.search_query == "tesla stock price"

    def test_classification_defaults_to_none(self):
        result = ClassificationResult(task_type="functional")
        assert result.search_query is None


class _RecordingSearch:
    """Search double returning one relevant + one unrelated result."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.enabled = True

    @property
    def backend_name(self) -> str:
        return "test"

    @property
    def max_results_for_extraction(self) -> int:
        return 5

    async def search(self, query: str, num_results: int = 5):
        results, _ = await self.search_with_info(query, num_results)
        return results

    async def search_with_info(
        self, query: str, num_results: int = 5, llm_client=None, user_consent=False
    ):
        self.queries.append(query)
        results = [
            SearchResult(
                title="guitar strings buying guide",
                url="https://good.com/guitar-strings",
                snippet="how to choose guitar strings",
                engine="test",
            ),
            SearchResult(
                title="pasta carbonara recipe",
                url="https://bad.com/pasta",
                snippet="italian cooking",
                engine="test",
            ),
        ]
        from assistant.backend.pipeline.search import SearchInfo
        return results, SearchInfo(backend="test", query=query, results=results)


@pytest.mark.asyncio
async def test_orchestrator_search_turn_excludes_irrelevant_links(store, stub_llm):
    """E2E guard: unrelated links never reach the system prompt or citations."""
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps

    spy = _RecordingSearch()
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=stub_llm),
            llm_client=stub_llm,
            search_tool=spy,
        )
    )
    user = await store.create_user("alice")
    # The query that leaves is the *distilled* one, not the raw message. The
    # conftest stub returns a canned chat response, so the distiller is patched.
    with patch(
        "assistant.backend.pipeline.search.distill_search_query",
        new=AsyncMock(return_value="guitar strings Austin"),
    ):
        response = await orchestrator.chat(
            ChatRequest(
                user_id=user.id,
                message="What guitar strings should I buy for my guitar?",
            )
        )

    # The search ran on the distilled query, not the raw conversational message.
    assert len(spy.queries) == 1
    assert spy.queries[0] != "What guitar strings should I buy for my guitar?"
    assert "guitar strings" in spy.queries[0].lower()

    # Relevant result survived the gate; unrelated one did not.
    assert any("good.com" in c for c in response.citations)
    assert all("bad.com" not in c for c in response.citations)

    injected = "\n".join(stub_llm.system_prompts)
    assert "https://good.com/guitar-strings" in injected
    assert "bad.com/pasta" not in injected


class TestBraveImageSearch:
    """Brave's image index: query-relevant images for the hero."""

    @pytest.mark.asyncio
    async def test_maps_brave_image_shape(self, monkeypatch):
        from assistant.backend.pipeline import search as s

        sample = {
            "results": [
                {
                    "title": "A guitar",
                    "url": "https://page/a",
                    "source": "site.com",
                    "properties": {"url": "https://img/full.jpg"},
                    "thumbnail": {"src": "https://cdn/500.jpg"},
                }
            ]
        }

        async def fake_get_json(client, url, params=None, headers=None):
            assert "images/search" in url
            assert params["safesearch"] == "strict"  # image search: off|strict only
            return sample

        monkeypatch.setattr(s, "_get_json", fake_get_json)
        backend = s.BraveBackend(api_key="k")
        try:
            images = await backend.search_images("guitar strings", 5)
        finally:
            await backend.close()

        assert len(images) == 1
        assert images[0].thumbnail == "https://cdn/500.jpg"
        assert images[0].image == "https://img/full.jpg"
        assert images[0].url == "https://page/a"

    @pytest.mark.asyncio
    async def test_default_backend_returns_no_images(self):
        """SearXNG has no image index; the ABC default returns []."""
        backend = SearXNGBackend(base_url="http://127.0.0.1:8080")
        assert await backend.search_images("q") == []

    def test_payload_includes_image_results(self):
        import json

        from assistant.backend.pipeline.search import (
            SearchInfo,
            SearchResult,
            search_info_payload,
        )

        info = SearchInfo(
            backend="brave",
            query="q",
            results=[],
            image_results=[
                SearchResult(
                    title="T",
                    url="https://page",
                    snippet="site.com",
                    engine="brave-images",
                    thumbnail="https://cdn/500.jpg",
                    image="https://img/full.jpg",
                )
            ],
        )
        payload = json.loads(search_info_payload(info))
        assert payload["image_results"][0]["thumbnail"] == "https://cdn/500.jpg"
        assert payload["image_results"][0]["image"] == "https://img/full.jpg"


class TestOrchestratorUsesSanitizedQuery:
    def test_search_module_exports_complete(self):
        # Lock the public surface other modules rely on.
        from assistant.backend.pipeline import search as s

        for name in (
            "sanitize_query",
            "distill_search_query",
            "normalize_url",
            "filter_relevant",
        ):
            assert hasattr(s, name)
