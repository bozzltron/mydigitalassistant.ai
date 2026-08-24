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
    """Abstract search backend. Implement search() and health_check()."""

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


class WebSearchTool(SearchBackend):
    """Default search tool using SearXNG. Backwards-compatible wrapper."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        enabled: bool = True,
    ):
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

    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        if not self.enabled:
            logger.warning("Search is disabled in config")
            return []
        return await self._backend.search(query, num_results)
