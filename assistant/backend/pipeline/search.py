"""Web search tool using SearXNG for privacy-first information retrieval."""

import logging
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


class WebSearchTool:
    """Search tool using SearXNG. Only works when enabled in config."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", enabled: bool = True):
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
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
        """Check if SearXNG is reachable."""
        if not self.enabled:
            return False
        try:
            client = await self._get_client()
            r = await client.get("/health")
            return r.status_code == 200
        except Exception:
            return False

    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        """Search SearXNG and return structured results."""
        if not self.enabled:
            logger.warning("Search is disabled in config")
            return []

        try:
            client = await self._get_client()
            params = {
                "q": query,
                "format": "json",
            }
            r = await client.get(f"{self.base_url}/search", params=params)
            r.raise_for_status()
            data = r.json()

            results = []
            engines_seen = set()
            for engine in ["google", "bing", "duckduckgo", "brave"]:
                engines_seen.add(engine)

            for key in ["results"]:
                items = data.get(key, [])
                for item in items:
                    if item.get("engine") in engines_seen:
                        results.append(
                            SearchResult(
                                title=item.get("title", ""),
                                url=item.get("url", ""),
                                snippet=item.get("content", ""),
                                engine=item.get("engine", "unknown"),
                            )
                        )
                    if len(results) >= num_results:
                        break
                if len(results) >= num_results:
                    break

            return results

        except Exception as e:
            logger.error("Search failed: %s", e)
            return []
