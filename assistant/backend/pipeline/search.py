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

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

import httpx

from assistant.backend.config import settings
from assistant.backend.retry import retry_transient

logger = logging.getLogger(__name__)


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
) -> dict:
    """GET JSON, raising on a non-2xx status.

    A named function so ``retry_transient`` can call it again from scratch.
    """
    response = await client.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()


class QuerySensitivity(Enum):
    """Result of sensitivity analysis for a search query."""
    SAFE = "safe"           # General knowledge, no PII/sensitive data
    SENSITIVE = "sensitive" # Contains PII, medical, financial, credentials, etc.
    AMBIGUOUS = "ambiguous" # Unclear - err on side of caution


@dataclass
class SensitivityResult:
    """Result of query sensitivity analysis."""
    level: QuerySensitivity
    reason: str
    categories: list[str]  # e.g., ["pii_email", "medical", "financial"]


async def classify_query_sensitivity(
    query: str,
    llm_client,
) -> SensitivityResult:
    """Classify a search query for privacy/sensitivity concerns.
    
    Uses the utility model for fast, cheap classification.
    Runs before any external search call.
    """
    from assistant.backend.pipeline.llm_client import ChatMessage
    
    system = ChatMessage(
        role="system",
        content="""Analyze the search query for privacy/sensitivity concerns.
        
Return JSON with:
- "level": "safe" | "sensitive" | "ambiguous"
- "reason": brief explanation
- "categories": list of concern categories found

Categories to watch for:
- "pii_email": email addresses
- "pii_phone": phone numbers  
- "pii_address": physical addresses
- "pii_name": PRIVATE individuals' full names (NOT public figures,
  celebrities, historical figures, authors, politicians, actors,
  musicians, athletes, etc.)
- "pii_ssn": social security / national ID numbers
- "pii_dob": date of birth
- "medical": health conditions, medications, diagnoses
- "financial": bank accounts, credit cards, income, investments
- "credentials": passwords, API keys, tokens, secrets
- "legal": ongoing legal matters, case numbers
- "location_tracking": precise real-time location
- "private_comms": private messages, emails, DMs
- "biometric": fingerprints, DNA, facial recognition data

PUBLIC FIGURE NAMES ARE SAFE (not PII):
- Celebrities, actors, musicians, athletes (e.g. "Tom Hanks", "Taylor Swift", "LeBron James")
- Historical figures (e.g. "Abraham Lincoln", "Marie Curie")
- Politicians, public officials (e.g. "Joe Biden", "Angela Merkel")
- Authors, creators, influencers with public presence
- Fictional characters (e.g. "Harry Potter", "Batman")

SAFE examples:
- "capital of France"
- "Python async tutorial"
- "weather in London"
- "best restaurants in Tokyo"
- "quantum computing explained"
- "youtube video of jason lee"  (actor)
- "movies starring tom hanks"
- "books by stephen king"
- "speeches by martin luther king"

SENSITIVE examples:
- "john.doe@company.com email"
- "SSN 123-45-6789"
- "my blood pressure medication"
- "credit card ending in 4242"
- "password for aws account"
- "divorce case number 2024-CV-12345"
- "exact location of my home"
- "my neighbor john smith's phone number"  (private individual)

AMBIGUOUS: unclear intent, err on side of caution.

Respond with ONLY valid JSON.""",
    )
    user = ChatMessage(role="user", content=f"Query: {query}")
    
    for attempt in range(3):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
                think=False,
            )
            if not response.content or not response.content.strip():
                logger.warning(
                    "Sensitivity classification returned empty content (attempt %d)",
                    attempt + 1,
                )
                if attempt < 2:
                    extra = (
                        "\n\nIMPORTANT: Output ONLY valid JSON. "
                        "No markdown, no preamble. Do not output empty response."
                    )
                    system = ChatMessage(role="system", content=system.content + extra)
                continue
            data = json.loads(response.content)
            level_str = data.get("level", "ambiguous").lower()
            valid_levels = ("safe", "sensitive", "ambiguous")
            level = (
                QuerySensitivity(level_str)
                if level_str in valid_levels
                else QuerySensitivity.AMBIGUOUS
            )
            return SensitivityResult(
                level=level,
                reason=data.get("reason", ""),
                categories=data.get("categories", []),
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "Sensitivity classification parse failed (attempt %d): %s",
                attempt + 1,
                e,
            )
            if attempt < 2:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=system.content + extra)
            else:
                logger.error(
                    "Sensitivity classification failed after retries (query_len=%d)",
                    len(query),
                )
    return SensitivityResult(
        level=QuerySensitivity.AMBIGUOUS,
        reason="Classification error after retries",
        categories=[],
    )

# Tracking/query junk stripped during URL normalization so that the same page
# reached through different campaign links dedups to one entry.
_TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "msclkid", "ref_", "mc_")
_FILLER_PATTERN = re.compile(
    r"\b(hey|hi|hello|please|thanks|thank you|can you|could you|would you|"
    r"look up|search for|find me|tell me about|do you know|i wonder|"
    r"for me|right now|quickly|real quick)\b",
    re.IGNORECASE,
)


def _youtube_id(url: str) -> str | None:
    """Extract a YouTube video id from any common URL form."""
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return None
    for pattern in (
        r"(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/shorts\/)([a-zA-Z0-9_-]{11})",
        r"youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})",
    ):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _thumb_urls(item: dict) -> tuple[str | None, str | None]:
    """(thumbnail, full_image) URLs from a result item.

    Engines disagree on the shape: Brave returns ``thumbnail`` as an object
    ``{src, original}`` (src = preview, original = full image), while others
    return flat strings. Accept both, and fall back to ``image``.
    """
    thumb = item.get("thumbnail")
    if isinstance(thumb, str):
        return thumb, thumb
    if isinstance(thumb, dict):
        src = thumb.get("src") or thumb.get("original")
        original = thumb.get("original") or thumb.get("src")
        return (str(src) if src else None, str(original) if original else None)
    image = item.get("image")
    if isinstance(image, str):
        return image, image
    if isinstance(image, dict):
        src = image.get("src") or image.get("original")
        original = image.get("original") or image.get("src")
        return (str(src) if src else None, str(original) if original else None)
    return None, None


def _absolute(url: str, base: str) -> str:
    """Resolve a possibly-relative URL (SearXNG image_proxy) against its host."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return base.rstrip("/") + url
    return url


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str
    engine: str
    thumbnail: str | None = None
    image: str | None = None


@dataclass
class YouTubeVideo:
    """A YouTube video result from search."""
    video_id: str
    title: str
    channel_title: str | None = None
    thumbnail_url: str | None = None
    url: str | None = None
    published_at: str | None = None
    duration: str | None = None


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
    sensitivity: SensitivityResult | None = None  # sensitivity analysis result
    consent_required: bool = False  # true if sensitive query needs user consent
    video_results: list[YouTubeVideo] | None = None
    # Query-relevant images (Brave's image index), used for the message hero.
    image_results: list[SearchResult] | None = None


def search_info_payload(search_info: "SearchInfo | None", max_results: int = 20) -> str | None:
    """A compact JSON projection of a search, for episode persistence.

    Only what the UI renders is kept — result title/url/thumbnail/image and the
    video fields — so the episode row stays small. Page snippets and engine
    scores are dropped: the UI never shows them and they are the bulk of the
    payload.
    """
    if search_info is None:
        return None
    payload = {
        "backend": search_info.backend,
        "query": search_info.query,
        "results": [
            {
                "title": r.title,
                "url": r.url,
                "thumbnail": r.thumbnail,
                "image": r.image,
            }
            for r in (search_info.results or [])[:max_results]
        ],
        "video_results": [
            {
                "video_id": v.video_id,
                "title": v.title,
                "channel_title": v.channel_title,
                "thumbnail_url": v.thumbnail_url,
                "url": v.url,
            }
            for v in (search_info.video_results or [])[:max_results]
        ],
        "image_results": [
            {
                "title": i.title,
                "url": i.url,
                "thumbnail": i.thumbnail,
                "image": i.image,
            }
            for i in (search_info.image_results or [])[:max_results]
        ],
    }
    return json.dumps(payload)


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


_DISTILL_PROMPT = """Turn a request into ONE web-search query.

Write a short, specific query (3-12 keywords) that would find the information
needed. Use the concrete subject -- names, titles, dates, places, product
models -- and drop the instruction words ("search for", "monitor", "check",
"research", "tell me", "find out", "look out for"). Never search for how to
search or how to research.

If the request has no concrete external target -- it is about the user's own
files or memory, or it is too vague to search ("prices for these items",
"findings first", "latest news") -- reply with exactly: NONE

Output only the query, or NONE."""


async def distill_search_query(text: str, llm_client) -> str | None:
    """A focused search query for ``text``, or None when there is no clear target.

    The raw message is a poor query: a scheduled task's script ("Monitor and alert
    for Mozilla release dates...") or a vague utterance ("prices for these items")
    makes the engine search for the *instruction*, not the subject -- which is how
    "search how to research" results appear. The router distils a query when it
    wants search; when it does not (forced/scheduled search, ``skip_route``), this
    is the fallback. It may decline, so a turn with no concrete target does not
    search at all.
    """
    from assistant.backend.pipeline.llm_client import ChatMessage

    try:
        resp = await llm_client.chat(
            [
                ChatMessage(role="system", content=_DISTILL_PROMPT),
                ChatMessage(role="user", content=text),
            ],
            model=llm_client.utility_model,
            temperature=0.0,
            think=False,
        )
    except Exception as e:
        logger.warning("Search-query distillation failed: %s", e)
        return None

    raw = (resp.content or "").strip()
    if not raw or raw.upper().startswith("NONE"):
        return None
    return sanitize_query(raw)


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

    Now batches embeddings for better performance.
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
        
        # Batch embed all result texts at once
        texts = [f"{r.title}. {r.snippet}"[:1000] for r in results]
        rvecs = await embed_fn(texts)
        
        kept: list[SearchResult] = []
        for r, rvec in zip(results, rvecs, strict=True):
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

    async def search_images(self, query: str, num_results: int = 8) -> list["SearchResult"]:
        """Query-relevant images, when the backend has an image index.

        Default: none. Brave overrides this — its image index returns images that
        match the *query* (with a reliable ~500px CDN copy), where a web result's
        og:image is chosen for social sharing and is often smaller and less
        relevant.
        """
        return []

    @abstractmethod
    async def search(
        self, query: str, num_results: int = 5
    ) -> tuple[list[SearchResult], list[YouTubeVideo]]:
        """Search and return structured results and video results."""

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

    async def search(
        self, query: str, num_results: int = 5
    ) -> tuple[list[SearchResult], list[YouTubeVideo]]:
        try:
            client = await self._get_client()
            params: dict = {
                "q": sanitize_query(query),
                "format": "json",
                "safesearch": settings.search_safesearch,
            }
            if settings.search_language:
                params["language"] = settings.search_language
            data = await retry_transient(
                lambda: _get_json(client, f"{self.base_url}/search", params=params),
                label="searxng search",
            )

            # Rank by SearXNG's merged engine score before slicing — the raw
            # result order interleaves engines and is not quality-sorted.
            def _score(item: dict) -> float:
                s = item.get("score", 0)
                return s if isinstance(s, (int, float)) else 0

            items = sorted(data.get("results", []), key=_score, reverse=True)

            results: list[SearchResult] = []
            video_results: list[YouTubeVideo] = []
            seen_urls: set[str] = set()
            for item in items:
                url = item.get("url", "")
                if not url:
                    continue
                norm = normalize_url(url)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                
                # Image: SearXNG exposes image results as img_src (full) and
                # thumbnail_src (preview), often relative when image_proxy is on.
                thumbnail, image = _thumb_urls(item)
                img_src = item.get("img_src")
                thumb_src = item.get("thumbnail_src")
                if isinstance(img_src, str) and img_src:
                    image = _absolute(img_src, self.base_url)
                    thumbnail = _absolute(
                        thumb_src if isinstance(thumb_src, str) and thumb_src else img_src,
                        self.base_url,
                    )

                video_id = _youtube_id(url)
                if video_id:
                    video_results.append(YouTubeVideo(
                        video_id=video_id,
                        title=item.get("title", ""),
                        channel_title=None,
                        thumbnail_url=thumbnail
                        or f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                        url=url,
                    ))

                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("content", ""),
                        engine=item.get("engine", "searxng"),
                        thumbnail=thumbnail,
                        image=image,
                    )
                )
                if len(results) >= num_results:
                    break

            logger.debug(
                "SearXNG returned %d results for %d candidates (query_len=%d)",
                len(results), len(items), len(params["q"]),
            )
            return results, video_results
        except Exception as e:
            logger.error("SearXNG search failed: %s", e)
            return [], []


class BraveBackend(SearchBackend):
    """Brave Search API (https://api.search.brave.com). Requires brave_api_key."""

    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
    BRAVE_IMAGES_URL = "https://api.search.brave.com/res/v1/images/search"
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

    async def search(
        self, query: str, num_results: int = 5
    ) -> tuple[list[SearchResult], list[YouTubeVideo]]:
        try:
            client = await self._get_client()
            headers = {**self.BRAVE_HEADERS, "X-Subscription-Token": self.api_key}
            params = {
                "q": sanitize_query(query),
                "count": min(num_results, 20),
                "safesearch": "moderate",
                "search_lang": settings.search_language or "en",
            }
            data = await retry_transient(
                lambda: _get_json(
                    client, self.BRAVE_URL, params=params, headers=headers
                ),
                label="brave search",
            )
            web_results = data.get("web", {}).get("results", [])
            if not web_results:
                # Must match the ABC's (results, videos) contract. A bare `[]`
                # here raised "not enough values to unpack" in the caller, so
                # every empty Brave response surfaced as a search failure
                # instead of "no results".
                return [], []
            results: list[SearchResult] = []
            video_results: list[YouTubeVideo] = []
            seen: set[str] = set()
            for item in web_results:
                url = item.get("url", "")
                if not url:
                    continue
                norm = normalize_url(url)
                if norm in seen:
                    continue
                seen.add(norm)

                # Brave returns `thumbnail` as {src, original} (an object), not
                # a string; _thumb_urls normalises that to (preview, full).
                thumbnail, image = _thumb_urls(item)

                video_id = _youtube_id(url)
                if video_id:
                    video_results.append(
                        YouTubeVideo(
                            video_id=video_id,
                            title=item.get("title", ""),
                            channel_title=(item.get("meta") or {}).get("author"),
                            thumbnail_url=thumbnail
                            or f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                            url=url,
                        )
                    )

                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("description", "") or item.get("snippet", ""),
                        engine="brave",
                        thumbnail=thumbnail,
                        image=image,
                    )
                )
                if len(results) >= num_results:
                    break

            # Brave attaches a `videos` section to the web response for
            # video-ish queries -- free, no second request. Merge it with the
            # YouTube-URL results captured above, deduped by video id.
            seen_video_ids = {v.video_id for v in video_results}
            for vitem in (data.get("videos") or {}).get("results") or []:
                vurl = vitem.get("url", "") or ""
                vthumb, _ = _thumb_urls(vitem)
                vid = _youtube_id(vurl)
                if vid:
                    if vid in seen_video_ids:
                        continue
                    seen_video_ids.add(vid)
                video_meta = vitem.get("video") or {}
                video_results.append(
                    YouTubeVideo(
                        video_id=vid or "",
                        title=vitem.get("title", ""),
                        channel_title=video_meta.get("creator")
                        or video_meta.get("publisher"),
                        thumbnail_url=vthumb,
                        url=vurl or None,
                    )
                )

            logger.debug(
                "Brave returned %d results (query_len=%d)",
                len(results), len(params["q"]),
            )
            return results, video_results
        except Exception as e:
            # Same contract as above: on error, return an empty 2-tuple rather
            # than an empty list, so the caller's unpack does not raise and turn
            # a Brave outage into an exception. `WebSearchTool.search_with_info`
            # still records the error for the trace panel.
            logger.error("Brave search failed: %s", e)
            return [], []

    async def search_images(
        self, query: str, num_results: int = 8
    ) -> list[SearchResult]:
        """Query-relevant images from Brave's image index.

        ``url`` is the page the image came from, ``thumbnail`` is Brave's ~500px
        CDN copy (reliable), ``image`` is the source image (full, sometimes
        blocked). Web-result thumbnails are ~200px and their og:image is chosen
        for social sharing, so this is both larger and more relevant for the hero.
        """
        try:
            client = await self._get_client()
            headers = {**self.BRAVE_HEADERS, "X-Subscription-Token": self.api_key}
            params = {
                "q": sanitize_query(query),
                "count": min(num_results, 20),
                # Image search accepts only off|strict (not "moderate").
                "safesearch": "strict",
                "search_lang": settings.search_language or "en",
            }
            data = await retry_transient(
                lambda: _get_json(
                    client, self.BRAVE_IMAGES_URL, params=params, headers=headers
                ),
                label="brave image search",
            )
        except Exception as e:
            logger.warning("Brave image search failed: %s", e)
            return []

        images: list[SearchResult] = []
        seen: set[str] = set()
        for item in data.get("results", []):
            props = item.get("properties") or {}
            thumb = item.get("thumbnail") or {}
            full = props.get("url")
            preview = thumb.get("src")
            if not preview and not full:
                continue
            key = full or preview
            if key in seen:
                continue
            seen.add(key)
            images.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("source", ""),
                    engine="brave-images",
                    thumbnail=preview,
                    image=full,
                )
            )
            if len(images) >= num_results:
                break
        logger.debug(
            "Brave returned %d images (query_len=%d)", len(images), len(params["q"])
        )
        return images


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

    async def search(
        self, query: str, num_results: int = 5
    ) -> tuple[list[SearchResult], list[YouTubeVideo]]:
        """Search and return results with video results."""
        if not self.enabled:
            logger.warning("Search is disabled in config")
            return [], []
        return await self._backend.search(sanitize_query(query), num_results)

    async def search_with_info(
        self, query: str, num_results: int = 5, llm_client=None, user_consent: bool = False
    ) -> tuple[list[SearchResult], SearchInfo]:
        """Search and return results with full provenance metadata.

        Returns (results, search_info) so the caller can record transparency data.
        Includes sensitivity analysis — if Brave backend and query is sensitive,
        consent_required will be True (caller should prompt user).
        
        IMPORTANT: If consent_required is True, the search is NOT executed.
        Caller must get user consent and retry with user_consent=True.
        """
        if not self.enabled:
            logger.warning("Search is disabled in config")
            return [], SearchInfo(
                backend=self.backend_name,
                query=query,
                results=[],
            )
        
        raw_query = sanitize_query(query)
        
        # Sensitivity check for Brave backend - BEFORE searching
        sensitivity = None
        consent_required = False
        if self.backend_name == "brave" and llm_client and not user_consent:
            sensitivity = await classify_query_sensitivity(raw_query, llm_client)
            sensitive_levels = (
                QuerySensitivity.SENSITIVE,
                QuerySensitivity.AMBIGUOUS,
            )
            consent_required = sensitivity.level in sensitive_levels
            if consent_required:
                logger.debug(
                    "Brave search query flagged as %s (query_len=%d, reason: %s, "
                    "categories: %s) - NOT executing",
                    sensitivity.level.value,
                    len(raw_query),
                    sensitivity.reason,
                    sensitivity.categories,
                )
                # Return early WITHOUT executing search - caller must get consent
                return [], SearchInfo(
                    backend=self.backend_name,
                    query=raw_query,
                    results=[],
                    sensitivity=sensitivity,
                    consent_required=True,
                )
        
        # Web and image results are independent requests: run them together so
        # the turn waits for the slower one, not for the sum. Both are graceful
        # (empty on error), and the sensitivity/consent gate above has already
        # run, so a sensitive query never reaches either call.
        (results, video_results), image_results = await asyncio.gather(
            self._backend.search(raw_query, num_results),
            self._backend.search_images(raw_query, num_results),
        )
        info = SearchInfo(
            backend=self.backend_name,
            query=raw_query,
            results=results,
            sensitivity=sensitivity,
            consent_required=consent_required,
            video_results=video_results,
            image_results=image_results,
        )
        return results, info
