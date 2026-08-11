"""Search tool with swappable backends: SearXNG (self-hosted) or Brave Search API.

The SearchBackend abstract class defines the interface. Swap backends via config
without changing the orchestrator or extractor.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str
    engine: str


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

    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
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
            params = {"q": query, "format": "json"}
            r = await client.get(f"{self.base_url}/search", params=params)
            r.raise_for_status()
            data = r.json()

            results: list[SearchResult] = []
            seen_urls: set[str] = set()
            for item in data.get("results", []):
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
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
