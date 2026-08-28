"""Search tool with swappable backends: SearXNG (self-hosted) or Brave Search API.

The SearchBackend abstract class defines the interface. Swap backends via config
without changing the orchestrator or extractor.

Result quality is guarded in three layers:
- sanitize_query() strips conversational filler before the query leaves town.
- SearXNGBackend ranks by engine score, dedups normalized URLs, and pins
  safesearch/language.
- filter_relevant() drops results whose embeddings sit too far from the query,
  so unrelated links never reach the system prompt.
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from assistant.backend.config import settings

logger = logging.getLogger(__name__)

# Tracking/query junk stripped during URL normalization so that the same page
# reached through different campaign links dedups to one entry.
_TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "msclkid", "ref_", "mc_")
_FILLER_PATTERN = re.compile(
    r"\b(hey|hi|hello|please|thanks|thank you|can you|could you|would you|"
    r"look up|search for|find me|tell me about|do you know|i wonder|"
    r"for me|right now|quickly|real quick)\b",
    re.IGNORECASE,
)


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str
    engine: str


@dataclass
class SearchInfo:
    """Full record of a search operation — backend, query, and results.

    Exposed in ChatResponse so the UI can show end-to-end transparency:
    which engine was used, what was searched, and which source each
    result came from (Brave's own index vs Bing etc.).
    """
    backend: str  # "brave" or "searxng"
    query: str  # sanitized query that was sent to the backend
    results: list[SearchResult]


def sanitize_query(query: str) -> str:
    """Reduce conversational text to a search-engine-friendly query.

    Strips polite filler and meta-language ("can you look up..."), collapses
    whitespace, and caps length so a rambling message doesn't dilute every
    term in the index lookup.
    """
    cleaned = _FILLER_PATTERN.sub(" ", query)
    cleaned = re.sub(r"[?!.,;:]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    tokens = cleaned.split()
    if len(tokens) > 16:
        cleaned = " ".join(tokens[:16])
    return cleaned


def normalize_url(url: str) -> str:
    """Canonical form for dedup: no fragments, no tracking params."""
    try:
        parts = urlsplit(url.strip())
        query_pairs = [
            (k, v)
            for k, v in [
                pair.split("=", 1) if "=" in pair else (pair, "")
                for pair in parts.query.split("&")
                if pair
            ]
            if not any(k.lower().startswith(p) for p in _TRACKING_PARAM_PREFIXES)
        ]
        clean_query = "&".join(
            f"{k}={v}" if v else k for k, v in query_pairs
        )
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, clean_query, ""))
    except ValueError:
        return url


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def filter_relevant(
    results: list[SearchResult],
    query: str,
    embed_fn,
    min_relevance: float | None = None,
) -> list[SearchResult]:
    """Drop results whose title+snippet embed too far from the query.

    This is the guard against unrelated links: ranking got us candidates,
    embedding similarity decides what actually belongs. Graceful by design —
    if the embedder is unavailable or errors, everything passes through
    rather than silently blinding search.
    """
    if not results or embed_fn is None:
        return results
    threshold = (
        min_relevance
        if min_relevance is not None
        else settings.search_min_relevance
    )
    try:
        qvec = await embed_fn(sanitize_query(query))
        kept: list[SearchResult] = []
        for r in results:
            text = f"{r.title}. {r.snippet}"[:1000]
            rvec = await embed_fn(text)
            score = _cosine(qvec, rvec)
            if score >= threshold:
                kept.append(r)
            else:
                logger.info(
                    "Dropped irrelevant result (%.2f < %.2f): %s",
                    score, threshold, r.url[:120],
                )
        return kept
    except Exception as e:
        logger.warning("Relevance filter unavailable (%s); keeping all results", e)
        return results


class SearchBackend(ABC):
    """Abstract search backend. Implement search(), health_check(), and backend_name."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short name of this backend, e.g. "brave" or "searxng"."""

    @property
    def max_results_for_extraction(self) -> int:
        """Backend-specific result budget for extraction prompts.

        Brave's index is cleaner; we can extract more aggressively from it.
        """
        return 5

    @abstractmethod
    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        """Search and return structured results."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the backend is reachable."""


class SearXNGBackend(SearchBackend):
    """SearXNG meta-search engine (self-hosted, privacy-first)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        timeout: float | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else settings.search_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def backend_name(self) -> str:
        return "searxng"

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            r = await client.get(f"{self.base_url}/health")
            return r.status_code == 200
        except Exception:
            return False

    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        try:
            client = await self._get_client()
            params: dict = {
                "q": sanitize_query(query),
                "format": "json",
                "safesearch": settings.search_safesearch,
            }
            if settings.search_language:
                params["language"] = settings.search_language
            r = await client.get(f"{self.base_url}/search", params=params)
            r.raise_for_status()
            data = r.json()

            # Rank by SearXNG's merged engine score before slicing — the raw
            # result order interleaves engines and is not quality-sorted.
            def _score(item: dict) -> float:
                s = item.get("score", 0)
                return s if isinstance(s, (int, float)) else 0

            items = sorted(data.get("results", []), key=_score, reverse=True)

            results: list[SearchResult] = []
            seen_urls: set[str] = set()
            for item in items:
                url = item.get("url", "")
                if not url:
                    continue
                norm = normalize_url(url)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("content", ""),
                        engine=item.get("engine", "searxng"),
                    )
                )
                if len(results) >= num_results:
                    break

            logger.info(
                "SearXNG returned %d results for %d candidates (q=%r)",
                len(results), len(items), params["q"][:80],
            )
            return results
        except Exception as e:
            logger.error("SearXNG search failed: %s", e)
            return []


class BraveBackend(SearchBackend):
    """Brave Search API (https://api.search.brave.com). Requires brave_api_key."""

    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
    BRAVE_HEADERS = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }

    def __init__(self, api_key: str, timeout: float | None = None):
        self.api_key = api_key
        self.timeout = timeout if timeout is not None else settings.search_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def backend_name(self) -> str:
        return "brave"

    @property
    def max_results_for_extraction(self) -> int:
        """Brave results are cleaner; extract up to 8 slots from them (cap at 10)."""
        return min(8, 10)

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            r = await client.get(
                self.BRAVE_URL,
                params={"q": "test", "count": 1},
                headers={**self.BRAVE_HEADERS, "X-Subscription-Token": self.api_key},
            )
            return r.status_code == 200
        except Exception:
            return False

    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        try:
            client = await self._get_client()
            headers = {**self.BRAVE_HEADERS, "X-Subscription-Token": self.api_key}
            params = {
                "q": sanitize_query(query),
                "count": min(num_results, 20),
                "safesearch": "moderate",
                "search_lang": settings.search_language or "en",
            }
            r = await client.get(self.BRAVE_URL, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
            web_results = data.get("web", {}).get("results", [])
            if not web_results:
                return []
            results: list[SearchResult] = []
            seen: set[str] = set()
            for item in web_results:
                url = item.get("url", "")
                if not url:
                    continue
                norm = normalize_url(url)
                if norm in seen:
                    continue
                seen.add(norm)
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("description", "") or item.get("snippet", ""),
                        engine="brave",
                    )
                )
                if len(results) >= num_results:
                    break
            logger.info(
                "Brave returned %d results (q=%r)",
                len(results), params["q"][:80],
            )
            return results
        except Exception as e:
            logger.error("Brave search failed: %s", e)
            return []



class WebSearchTool(SearchBackend):
    """Default search tool using SearXNG. Backwards-compatible wrapper."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        enabled: bool = True,
    ):
        if settings.brave_enabled and settings.brave_api_key:
            self._backend = BraveBackend(api_key=settings.brave_api_key)
        elif settings.brave_enabled and not settings.brave_api_key:
            raise ValueError(
                "BRAVE_ENABLED=true but BRAVE_API_KEY is not set. "
                "Both settings are required to use Brave Search."
            )
        else:
            self._backend = SearXNGBackend(base_url=base_url)
        self.enabled = enabled

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._backend._get_client()

    async def close(self) -> None:
        await self._backend.close()

    async def health_check(self) -> bool:
        if not self.enabled:
            return False
        return await self._backend.health_check()

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name

    async def search_with_info(
        self, query: str, num_results: int = 5
    ) -> tuple[list[SearchResult], SearchInfo]:
        """Search and return results with full provenance metadata.

        Returns (results, search_info) so the caller can record transparency data.
        """
        if not self.enabled:
            logger.warning("Search is disabled in config")
            return [], SearchInfo(backend=self.backend_name, query=query, results=[])
        raw_query = sanitize_query(query)
        results = await self._backend.search(raw_query, num_results)
        info = SearchInfo(
            backend=self.backend_name,
            query=raw_query,
            results=results,
        )
        return results, info

    async def search(
        self, query: str, num_results: int = 5
    ) -> list[SearchResult]:
        """Legacy compat: returns results only. Prefer search_with_info()."""
        results, _ = await self.search_with_info(query, num_results)
        return results
