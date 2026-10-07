"""Tool framework (Phase 6 M5): native Ollama tool-calling on the chat model.

A minimal registry of household-safe, local-first tools. The chat model
decides when to call them; results are fed back until it answers in prose.
No cloud APIs — web_search goes through the local SearXNG instance only.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx
from pydantic import BaseModel, Field

from assistant.backend.config import settings
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import (
    EMPTY_GENERATION_FALLBACK,
    ChatMessage,
    OllamaClient,
    compact_messages,
)
from assistant.backend.pipeline.url_safety import UnsafeURLError, assert_public_url, safe_stream
from assistant.backend.retry import retry_transient

logger = logging.getLogger(__name__)


MAX_TOOL_ROUNDS = 3
MAX_FETCH_BYTES = 500_000
FETCH_TIMEOUT_SECONDS = 15

# The tool identifies itself honestly. robots.txt rules that name `*` or
# `AssistantBot` bind it; an AI-specific block naming another crawler does not
# bind it by letter, though it expresses the owner's intent about AI clients.
FETCH_USER_AGENT = "Mozilla/5.0 (compatible; AssistantBot/1.0)"
# The token a robots.txt `User-agent` line names us by. `robotparser` takes the
# part of the UA before the first "/", which for the string above is "Mozilla", so
# it must be told our product token explicitly -- otherwise a site that writes
# `User-agent: AssistantBot` would not bind us.
ROBOTS_USER_AGENT = "AssistantBot"


class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._bullets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "hr"):
            self._text.append("\n")
        elif tag in ("p", "div", "li"):
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "div"):
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._text.append(text)

    @property
    def text(self) -> str:
        joined = "".join(self._text)
        return " ".join(
            " ".join(line.split())
            for line in joined.split("\n")
            if line.strip()
        )


def _strip_html(html: str) -> str:
    try:
        extractor = _HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.text
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class AssistantTool:
    """A callable tool exposed to the chat model via the Ollama tools API."""

    name: str
    description: str
    parameters: dict  # JSON schema for the function arguments
    handler: Callable[..., Awaitable[str]]

    def to_def(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


async def _read_capped(response: httpx.Response, limit: int) -> bytes:
    """Read at most `limit` bytes off a streaming response (real cap)."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(chunks)[:limit]


_CRAWL_DELAY_SECONDS = 0.5


class _HTMLLinkExtractor(HTMLParser):
    """Collect `href`s from an HTML document, for bounded link-following."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.links.append(value)


def _snippet(text: str, limit: int = 3000) -> str:
    snippet = text[:limit]
    if len(text) > limit:
        snippet += f"\n... [{len(text):,} total characters, truncated to first {limit}]"
    return snippet


def _same_site_links(base_url: str, raw_html: str, max_candidates: int) -> list[str]:
    """Same-host `http(s)` links in `raw_html`, resolved and deduped.

    The fragment is dropped and the query kept; the base URL itself is excluded.
    """
    base = urllib.parse.urlparse(base_url)
    parser = _HTMLLinkExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for href in parser.links:
        resolved = urllib.parse.urlparse(urllib.parse.urljoin(base_url, href))
        if resolved.scheme not in ("http", "https") or resolved.netloc != base.netloc:
            continue
        clean = urllib.parse.urlunparse(
            (
                resolved.scheme,
                resolved.netloc,
                resolved.path,
                resolved.params,
                resolved.query,
                "",
            )
        )
        if clean == base_url or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= max_candidates:
            break
    return out


async def _follow_same_site(
    client: httpx.AsyncClient, base_url: str, raw_html: str, limit: int
) -> list[str]:
    """Fetch up to `limit` same-site links, robots-permitting, paced.

    "Going deeper" is not a crawl that hammers a site: same host only, deduped,
    robots.txt checked per URL, a short delay between fetches, and every hop goes
    through `safe_stream` (the SSRF check). A link that fails, is off-site, or is
    disallowed is skipped rather than aborting the whole fetch.
    """
    import asyncio

    blocks: list[str] = []
    for link in _same_site_links(base_url, raw_html, limit * 3):
        if len(blocks) >= limit:
            break
        try:
            if not await _check_robots_txt(client, link, ROBOTS_USER_AGENT):
                continue
            async with safe_stream(client, link) as r:
                if "text/html" not in r.headers.get("content-type", ""):
                    continue
                raw = (await _read_capped(r, MAX_FETCH_BYTES)).decode(
                    r.encoding or "utf-8", errors="replace"
                )
            text = _strip_html(raw)
            if not text.strip():
                continue
            blocks.append(f"## {link}\n{_snippet(text)}")
            await asyncio.sleep(_CRAWL_DELAY_SECONDS)
        except Exception:
            continue
    return blocks


async def _fetch_page(url: str) -> tuple[str, str, str | None]:
    """Fetch `url`: (plain_text, raw_html, error).

    THE one fetch path. The scheme and host are checked (`assert_public_url`),
    robots.txt is consulted, the body is read through `safe_stream` (which re-runs
    the SSRF check on every redirect hop), and HTML is stripped. Transient network
    errors are retried (a GET is idempotent); a robots refusal is returned, not
    retried.

    The `fetch_url` tool and search enrichment both go through here, so robots.txt
    and the SSRF check cannot drift between them.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "", "", f"Error: only http/https URLs are supported, got {parsed.scheme!r}"
    except Exception as e:
        return "", "", f"Error: malformed URL {e}"

    try:
        await assert_public_url(url)
    except UnsafeURLError as e:
        return "", "", f"Error: {e}"

    async def _do_fetch() -> tuple[str, str, str | None]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS, read=20.0),
            follow_redirects=False,
            headers={"User-Agent": FETCH_USER_AGENT},
        ) as client:
            if not await _check_robots_txt(client, url, ROBOTS_USER_AGENT):
                return "", "", f"Error: {url} is blocked by robots.txt"

            async with safe_stream(client, url) as r:
                content_type = r.headers.get("content-type", "")
                encoding = r.encoding or "utf-8"
                if "text/html" not in content_type and "text/plain" not in content_type:
                    body = (await _read_capped(r, 2000)).decode(
                        encoding, errors="replace"
                    )
                    return body, "", None

                raw = (await _read_capped(r, MAX_FETCH_BYTES)).decode(
                    encoding, errors="replace"
                )

            return _strip_html(raw), raw, None

    try:
        # A GET is idempotent, so a transient network blip is retried.
        return await retry_transient(_do_fetch, label=f"fetch_url {url}")
    except httpx.TimeoutException:
        return "", "", f"Error: timeout fetching {url} ({FETCH_TIMEOUT_SECONDS}s)"
    except Exception as e:
        return "", "", f"Error fetching {url}: {e}"


async def _fetch_single_url(url: str, follow_links: int = 0) -> str:
    """Fetch a URL, strip HTML, return plain text.

    With `follow_links > 0`, also fetches up to that many same-site links from the
    page (robots-permitting, paced) and appends them under `## <url>` headers -- a
    bounded "read further into this site", not a crawl.

    The scheme/host checks, robots.txt, the per-hop SSRF check and the retry live in
    `_fetch_page`; this adds the JS-rendered check and the crawl.
    """
    text, raw, error = await _fetch_page(url)
    if error:
        return error
    if not text.strip():
        return (
            "Error: page appears to be JavaScript-rendered "
            "(empty after HTML strip). Try searching for the page "
            "content instead."
        )

    parts = [_snippet(text)]
    if follow_links > 0 and raw:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS, read=20.0),
            follow_redirects=False,
            headers={"User-Agent": FETCH_USER_AGENT},
        ) as client:
            parts.extend(await _follow_same_site(client, url, raw, follow_links))
    return "\n\n".join(parts)


async def _check_robots_txt(
    client: httpx.AsyncClient, url: str, user_agent: str
) -> bool:
    """Whether `user_agent` may fetch `url`, per the site's robots.txt.

    Parsed with the stdlib `robotparser`, which understands per-User-agent and
    per-path rules. The previous check was a substring match: ``"disallow: /"`` is
    a substring of ``"disallow: /private/"``, so any site with a single
    path-specific rule was treated as fully disallowed -- which is nearly every
    site, and the reason the agent was "often blocked by robots.txt" while nothing
    had actually disallowed it.

    Defaults to allow when robots.txt is absent or unreadable: robots.txt is an
    opt-out, and its absence is not a prohibition.
    """
    from urllib.robotparser import RobotFileParser

    try:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        r = await client.get(robots_url, timeout=5.0)
        if r.status_code != 200:
            return True  # no robots.txt = allow
        parser = RobotFileParser()
        parser.parse(r.text.splitlines())
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True  # on error, allow


def _make_fetch_url_handler(
    store: MemoryStore | None = None,
    llm_client: OllamaClient | None = None,
):
    """Create a fetch_url handler that auto-extracts facts into memory.

    Returns an async callable: handler(url, follow_links=0) -> str
    """
    async def handler(url: str, follow_links: int = 0) -> str:
        content = await _fetch_single_url(url, follow_links)
        if content.startswith("Error:"):
            return content

        if store and llm_client:
            try:
                from assistant.backend.pipeline.extractor import (
                    apply_extraction,
                    extract_facts_from_document,
                )

                extraction = await extract_facts_from_document(
                    content, url, llm_client
                )
                if extraction.slots or extraction.associations:
                    await apply_extraction(
                        extraction,
                        store,
                        source_type="web_fetch",
                        source_url=url,
                        source_reliability=0.7,
                    )
                    logger.info(
                        "fetch_url: extracted %d slots, %d assocs from %s",
                        len(extraction.slots),
                        len(extraction.associations),
                        url,
                    )
            except Exception as e:
                logger.warning("fetch_url: auto-extract failed for %s: %s", url, e)

        return content

    return handler


# ---------------------------------------------------------------------------
# Schema definitions (validated via Pydantic before dispatch)
# ---------------------------------------------------------------------------

class UpsertSlotArgs(BaseModel):
    frame_name: str = Field(
        ..., description="Entity/concept name (e.g. 'person_john')")
    slot_key: str = Field(
        ..., description="Attribute name (e.g. 'birthday')")
    slot_value: str = Field(
        ..., description="Value to store")
    confidence: float = Field(
        0.5, ge=0, le=1, description="Initial confidence (0.5=uncertain)")
    essential: bool = Field(
        False, description="Protected from GC/forgetting")
    priority: int = Field(
        0, description="Higher = more important for retrieval")
    source_type: str = Field(
        "conversation",
        description="conversation | search | document | correction | inference",
    )
    source_episode_id: str | None = Field(
        None, description="Link to originating turn")


class UpsertAssociationArgs(BaseModel):
    source_frame: str = Field(
        ..., description="Frame name")
    target_frame: str = Field(
        ..., description="Frame name")
    relation_type: str = Field(
        ...,
        description="related | causes | part_of | instance_of | contradicts | supports | custom",
    )
    confidence: float = Field(
        0.5, ge=0, le=1, description="Confidence in association")
    bidirectional: bool = Field(
        False, description="Create reverse relation too")


class MarkEssentialArgs(BaseModel):
    frame_name: str = Field(
        ..., description="Frame to protect")
    slot_key: str | None = Field(
        None, description="Optional: protect specific slot only")
    essential: bool = Field(
        True, description="Set essential=true")


class RecallArgs(BaseModel):
    query: str = Field(
        ..., description="Natural language query (embedded for similarity)")
    frame_types: list[str] | None = Field(
        None, description="Optional filter: frame name patterns")
    max_results: int = Field(
        10, ge=1, description="Max frames to return")
    min_confidence: float = Field(
        0.3, ge=0, le=1, description="Minimum confidence threshold")
    include_associations: bool = Field(
        True, description="Walk graph to connected frames")


GetFrameArgs = dict[str, str]  # just frame_name

GetSlotHistoryArgs = dict[str, str]  # frame_name + slot_key


class SearchEpisodesArgs(BaseModel):
    query: str = Field(
        ..., description="Natural language query")
    session_id: str | None = Field(
        None, description="Restrict to session")
    max_results: int = Field(
        5, ge=1, description="Max episodes to return")


# External tool args

class WebSearchArgs(BaseModel):
    query: str = Field(
        ..., description="Sanitized search query")
    num_results: int = Field(
        5, ge=1, le=20, description="Number of results")
    min_relevance: float = Field(
        0.3, ge=0, le=1, description="Cosine similarity threshold")


class FetchUrlArgs(BaseModel):
    url: str = Field(
        ..., description="URL to fetch")
    extract_facts: bool = Field(
        True, description="Auto-extract facts into memory")
    follow_links: int = Field(
        0,
        ge=0,
        le=5,
        description=(
            "Also fetch up to this many same-site links from the page (0-5), to "
            "read further into a site. Robots.txt and same-host limits apply, and "
            "fetched pages are paced. Default 0 (just this page)."
        ),
    )


class ReadFileArgs(BaseModel):
    path: str | None = Field(
        None,
        description=(
            "Sandbox relative path. For uploaded files this is the file's exact "
            "name, e.g. 'subscribers_active.csv' (the frame name without the "
            "'file_' prefix). Stale or partial names also resolve to the "
            "matching uploaded file."
        ),
    )
    frame_id: int | None = Field(
        None,
        description=(
            "Frame ID of the uploaded file to read. Prefer frame_name — it is "
            "shown in memory context, e.g. file_subscribers_active.csv."
        ),
    )
    frame_name: str | None = Field(
        None,
        description=(
            "Frame name of the uploaded file, e.g. 'file_subscribers_active.csv'. "
            "Stale frame names quoted in old conversations resolve to the "
            "current file."
        ),
    )
    offset: int = Field(
        0,
        ge=0,
        description=(
            "0-based line to start at, for reading a large file in pieces. A "
            "capped result names the range and the offset for the next page."
        ),
    )
    limit: int | None = Field(
        None,
        ge=1,
        description="Maximum lines to return (default: to the end, capped).",
    )


class ListFilesArgs(BaseModel):
    pass


# File sandbox tool args
class WriteFileArgs(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Relative path in sandbox, WITH an extension (e.g. 'notes/todo.md', "
            "'report.docx', 'budget.xlsx'). The extension selects the format and "
            "the content is written as a real file of that type. Writable: txt, "
            "md, csv, json, ics, pdf, docx, odt, xlsx, xls, ods, pptx, odp. For a "
            "spreadsheet, pass comma-separated rows; for a calendar (.ics), pass "
            "a complete iCalendar document (BEGIN:VCALENDAR ... END:VCALENDAR) "
            "with one or more VEVENTs. Plain prose becomes a single event titled "
            "with its first line."
        ),
    )
    content: str = Field(..., description="File content to write")
    overwrite: bool = Field(False, description="Allow overwriting existing file")


class EditFileArgs(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Relative path of a TEXT file in the sandbox (e.g. 'notes/todo.md'). "
            "Binary documents (.docx, .pdf, .xlsx, ...) cannot be edited in place."
        ),
    )
    old_text: str = Field(
        ...,
        description=(
            "Exact text to replace. Read the file first and copy it verbatim; "
            "whitespace differences are tolerated, but the words must match."
        ),
    )
    new_text: str = Field(..., description="Replacement text")
    replace_all: bool = Field(True, description="Replace all occurrences (default: true)")


class DeleteFileArgs(BaseModel):
    path: str = Field(..., description="Relative path in sandbox")


class RenameFileArgs(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Sandbox-relative path of the file to rename, e.g. 'notes/todo.txt' "
            "or 'mazworth_calendar.ics'."
        ),
    )
    new_name: str = Field(
        ...,
        description=(
            "The new file name, e.g. 'mozworth_calendar.ics'. Keep the same "
            "extension. A bare name keeps the file in its folder; include a slash "
            "to move it (e.g. 'archive/tasks.txt')."
        ),
    )


class GlobArgs(BaseModel):
    pattern: str = Field(
        ..., description="Glob pattern (e.g., '*.txt', 'notes/**/*.md', '**/*.py')")


class RunScheduledTaskArgs(BaseModel):
    task_name: str = Field(
        ..., description="Name of task to run immediately")


class ComputeArgs(BaseModel):
    expression: str = Field(
        ..., description="Natural language description of computation needed")
    context: dict | None = Field(
        None, description="Optional: variable bindings (e.g., {'rate': 0.07, 'years': 10})")
    precision: int = Field(
        4, description="Decimal places in result")


# ---------------------------------------------------------------------------
# Meta tool args
# ---------------------------------------------------------------------------

class PlanArgs(BaseModel):
    goal: str = Field(
        ..., description="The overall goal/task")
    steps: list[dict] = Field(
        ...,
        description="List of {tool, args, description} steps to execute")


class ThinkArgs(BaseModel):
    reasoning: str = Field(
        ..., description="Free-form reasoning trace")
    confidence: float = Field(
        ..., ge=0, le=1, description="Confidence in current conclusion")


class FinalizeArgs(BaseModel):
    answer: str = Field(
        ..., description="Final response to user")
    extraction_summary: dict | None = Field(
        None, description="Optional: summarize what was learned")


# ---------------------------------------------------------------------------
# Builtin tools registry – returns LLM-ready tool definitions
# ---------------------------------------------------------------------------

def builtin_tools(
    search_tool=None,
    store: MemoryStore | None | None = None,
    llm_client: OllamaClient | None | None = None,
    embed_fn: Callable | None | None = None,
) -> list[dict]:
    """Return tool definitions for the Ollama tools API.

    Each entry is {"type": "function", "function": {name, description, parameters}}.
    The returned tools cover memory writes, reads, external searches, and meta ops.
    """
    def _make_def(name, description, args_model):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": args_model.model_json_schema(),
            },
        }

    tools = [
        _make_def(
            "list_files",
            "List all files in the assistant's sandbox directory. "
            "Returns file names, paths, types, sizes. "
            "Use for: discovering what files exist, getting a full inventory.",
            ListFilesArgs,
        ),
        _make_def(
            "write_file",
            (
                "Create a new file or overwrite an existing file in the sandbox. "
                "Use for: saving notes, creating scripts, writing reports, "
                "generating code, drafting documents. "
                "Path is relative to sandbox root (e.g., 'notes/meeting.txt', "
                "'scripts/analyze.py'). "
                "Set overwrite=true to replace existing file. Auto-creates parent "
                "directories."
            ),
            WriteFileArgs,
        ),
        _make_def(
            "read_file",
            "Read a file's contents: a sandbox file by relative path "
            "(e.g., 'notes/meeting.txt', 'data.csv'), or an uploaded file by its "
            "frame name (e.g., 'file_subscribers_active.csv') or frame_id. "
            "A large file is read in pieces: a capped result names the line range "
            "and the offset for the next page, so use offset/limit to walk it.",
            ReadFileArgs,
        ),
        _make_def(
            "edit_file",
            "Make a surgical edit to an existing TEXT file by replacing exact "
            "text. Use for small changes to text/markdown, configs, and code. "
            "Read the file first and copy old_text verbatim (whitespace is "
            "tolerated; the words must match). For a binary document (.docx, "
            ".pdf, .xlsx, ...) read it and rewrite the whole file with write_file "
            "instead — documents cannot be edited in place.",
            EditFileArgs,
        ),
        _make_def(
            "delete_file",
            "Delete a file from the sandbox. Also removes the associated memory frame. "
            "Use when a file is no longer needed. Path is relative to sandbox root.",
            DeleteFileArgs,
        ),
        _make_def(
            "rename_file",
            "Rename a file in the sandbox, keeping the same extension. Use for: "
            "fixing a name, correcting a spelling, or moving a file to a folder. "
            "The memory frame moves with it. Fails rather than overwriting an "
            "existing file.",
            RenameFileArgs,
        ),
        _make_def(
            "glob",
            "Find files matching a glob pattern in the sandbox. "
            "Use for: discovering files, listing directory contents, finding files by extension. "
            "Patterns: '*.txt' (all txt files), 'notes/*.md' (md files in notes/), "
            "'**/*.py' (all Python files recursively).",
            GlobArgs,
        ),
        _make_def(
            "upsert_slot",
            "Store or update a fact in memory. Creates frame if missing.",
            UpsertSlotArgs,
        ),
        _make_def(
            "upsert_association",
            "Create or strengthen a typed relation between two frames.",
            UpsertAssociationArgs,
        ),
        _make_def(
            "mark_essential",
            "Protect a frame/slot from garbage collection (e.g., user identity, critical facts).",
            MarkEssentialArgs,
        ),
        _make_def(
            "recall",
            "Semantic memory lookup. Returns frames/slots matching "
            "query via embedding similarity + graph walk. "
            "Use this when the user refers to something from earlier — 'the list', "
            "'those links', 'what I gave you', 'the document' — to find the content "
            "they supplied or that you stored. Prefer it over web_search for "
            "anything that came from the user or from this conversation.",
            RecallArgs,
        ),
        _make_def(
            "search_episodes",
            "Search past conversation turns semantically (episode_embeddings). "
            "Use this when the user refers back to something said or given earlier "
            "in the conversation — it finds the actual turns, including content the "
            "user pasted. Prefer it over web_search for anything the user supplied.",
            SearchEpisodesArgs,
        ),
        _make_def(
            "web_search",
            "Search the web via configured backend (SearXNG or Brave). "
            "Returns structured results with URLs.",
            WebSearchArgs,
        ),
        _make_def(
            "fetch_url",
            "Fetch and extract text from a URL. Auto-extracts facts into memory. "
            "Set follow_links to read further into the same site.",
            FetchUrlArgs,
        ),
        _make_def(
            "run_scheduled_task",
            "Execute a scheduled task by name immediately (run_now).",
            RunScheduledTaskArgs,
        ),
        _make_def(
            "compute",
            "Execute mathematical computation via dedicated math model. "
            "Use for: financial models (NPV, IRR), statistics, calculus, "
            "linear algebra, optimization, unit conversions, dimensional analysis.",
            ComputeArgs,
        ),
        _make_def(
            "plan",
            "Create a multi-step plan for complex tasks. "
            "Returns step list for orchestrator to execute.",
            PlanArgs,
        ),
        _make_def(
            "think",
            "Internal reasoning step. No external effect. "
            "Use to decompose problems, weigh evidence, self-correct.",
            ThinkArgs,
        ),
        _make_def(
            "finalize",
            "Signal completion. Return final answer to user. Ends the loop.",
            FinalizeArgs,
        ),
    ]

    # Gate web_search on search_tool being enabled
    if search_tool is not None and getattr(search_tool, "enabled", False):
        pass
    else:
        tools = [t for t in tools if t["function"]["name"] != "web_search"]

    # Gate compute on math_model being configured and supporting tools
    if (
        llm_client is None
        or not getattr(llm_client, "math_model", None)
        or not llm_client.math_model
    ):
        tools = [t for t in tools if t["function"]["name"] != "compute"]
    else:
        # Check if math_model supports tools (async check - we can't do it here synchronously)
        # The tool will just fail gracefully at runtime if the model doesn't support tools
        pass

    return tools


# ---------------------------------------------------------------------------
# Run the function-calling memory loop
# ---------------------------------------------------------------------------

async def run_tool_loop(
    llm_client: OllamaClient,
    messages: list[dict[str, str]],
    tools: list[dict],
    user_id: str = "",
    session_id: str = "",
    max_rounds: int | None = None,
    reasoning_effort: str = "medium",
    stream: bool = False,
    think: bool = False,
    num_predict: int | None = None,
    model: str | None = None,
) -> dict[str, object]:
    """Main function-calling memory loop.

    Runs until LLM calls finalize() or max_turns reached.
    Returns dict with answer, loop_terminated, memory_updated, turns,
    reasoning_effort.

    Uses the dedicated tools_model (1.5B) by default for faster tool calling.
    Uses async parallel execution for independent tool calls.
    """
    import logging

    from assistant.backend.pipeline.async_tools import (
        ToolCall as AsyncToolCall,
    )
    from assistant.backend.pipeline.async_tools import (
        execute_tools_parallel,
        format_tool_result,
    )

    logger = logging.getLogger(__name__)

    tool_names = [t["function"]["name"] for t in tools]
    logger.info("DEBUG run_tool_loop: Starting with tools: %s", tool_names)
    logger.debug("DEBUG run_tool_loop: Full tool definitions: %s", tools)

    # Define turn limits inline
    max_reasoning_turns: dict[str, int] = {
        "none": 0,
        "low": 1,
        "medium": 3,
        "high": 5,
    }
    reasoning_temperature: dict[str, float] = {
        "none": 0.7,
        "low": 0.5,
        "medium": 0.3,
        "high": 0.2,
    }

    # Determine max turns
    if max_rounds is not None:
        max_turns = max_rounds
    elif reasoning_effort not in max_reasoning_turns:
        reasoning_effort = "medium"
        max_turns = max_reasoning_turns[reasoning_effort]
    else:
        max_turns = max_reasoning_turns[reasoning_effort]

    temperature = reasoning_temperature[reasoning_effort]

    # Initialize loop state
    turn = 0
    tool_results: list[dict] = []
    reasoning_trace: list[str] = []
    empty_retried = False

    # Use tools_model (1.5B) by default for faster tool calling
    # Fall back to utility_model if tools_model is not available (e.g., in tests)
    loop_model = model or getattr(llm_client, "tools_model", None) or settings.utility_model

    # Main loop
    while turn < max_turns:
        turn += 1

        logger.info(
            "DEBUG run_tool_loop: Turn %d, calling LLM (%s) with %d messages, "
            "think=%s, num_predict=%s",
            turn,
            loop_model,
            len(messages),
            think,
            num_predict,
        )
        response = await llm_client.chat(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            model=loop_model,
            temperature=temperature,
            think=think,
            num_predict=num_predict,
        )

        logger.info("DEBUG run_tool_loop: LLM response: tool_calls=%s, content=%s", 
            bool(response.tool_calls), response.content[:100] if response.content else "None")

        # Empty generation with no tool call: on this stack that is context
        # exhaustion (the prompt filled the window and left no room to answer;
        # llama-server logs truncated=1, Ollama reports done_reason="length").
        # Retry once with history and prior tool chatter dropped. Without this
        # the loop returned the old comprehension-sounding fallback that hid
        # the cause.
        if (
            not response.tool_calls
            and not (response.content or "").strip()
            and not empty_retried
        ):
            empty_retried = True
            compacted = compact_messages(messages)
            logger.warning(
                "Empty generation (done_reason=%r); retrying with %d of %d "
                "messages after dropping history",
                response.done_reason,
                len(compacted),
                len(messages),
            )
            messages = compacted
            response = await llm_client.chat(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                model=loop_model,
                temperature=temperature,
                think=think,
                num_predict=num_predict,
            )

        # Check for tool calls
        if response.tool_calls:
            # Convert to async tool calls
            async_calls = [
                AsyncToolCall(name=call.name, arguments=call.arguments or {})
                for call in response.tool_calls
            ]

            # Execute tools in parallel where possible
            tool_results_list = await execute_tools_parallel(
                async_calls,
                user_id=user_id,
                session_id=session_id,
            )

            # Process results in order
            for call, result in zip(response.tool_calls, tool_results_list, strict=True):
                tool_name = call.name
                raw_args = call.arguments or {}

                logger.info("DEBUG run_tool_loop: Tool %s result: success=%s, data=%s, error=%s",
                    tool_name,
                    result.success,
                    str(result.data)[:200],
                    result.error)

                # finalize ends the loop immediately
                if tool_name == "finalize":
                    answer = raw_args.get(
                        "answer", "Thank you for the information."
                    )
                    return {
                        "answer": answer,
                        "loop_terminated": "finalize",
                        "memory_updated": True,
                        "turns": turn,
                        "reasoning_effort": reasoning_effort,
                        "reasoning_trace": (
                            "\n\n".join(reasoning_trace) if reasoning_trace else None
                        ),
                    }

                # Record tool result for next iteration
                tool_results.append(
                    {
                        "call": {"name": tool_name, "args": raw_args},
                        "result": {
                            "success": result.success,
                            "data": result.data,
                            "error": result.error,
                        },
                        "metadata": result.metadata or {},
                    }
                )

                # Record think() reasoning
                if tool_name == "think":
                    reasoning_trace.append(
                        raw_args.get("reasoning", ""))

                # Add tool result to messages for next iteration (surface the
                # error on failure so the model can recover instead of retrying
                # the exact same call)
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=format_tool_result(result),
                        name=tool_name
                    )
                )
        else:
            # No tool calls = direct answer (finalize)
            answer = (response.content or "").strip() or EMPTY_GENERATION_FALLBACK
            return {
                "answer": answer,
                "loop_terminated": "finalize",
                "memory_updated": len(tool_results) > 0,
                "turns": turn,
                "reasoning_effort": reasoning_effort,
                "reasoning_trace": (
                    "\n\n".join(reasoning_trace) if reasoning_trace else None
                ),
            }

    # Max turns reached - synthesize a graceful wrap-up from the model instead
    # of dumping raw tool metadata at the user. This extra LLM call only fires
    # on the already-failing path (per the "lean on the model" principle; a
    # neutral message is a last resort if even the wrap-up call fails).
    answer = None
    try:
        wrap_up = await llm_client.chat(
            messages=[
                *messages,
                ChatMessage(
                    role="user",
                    content=(
                        "You have used all your available tool-call turns "
                        "without producing a final answer. Wrap up for the "
                        "user now in plain language: state what you were able "
                        "to do, what blocked you (including any tool errors), "
                        "and what you need from them next. Do not describe "
                        "the tool loop internals or repeat raw tool data."
                    ),
                ),
            ],
            tools=tools,
            tool_choice="none",
            model=loop_model,
            temperature=temperature,
            think=think,
            num_predict=num_predict,
        )
        answer = (wrap_up.content or "").strip() or None
    except Exception:
        logger.warning(
            "Tool wrap-up LLM call failed; using graceful fallback",
            exc_info=True,
        )
    if answer is None:
        answer = (
            "I wasn't able to complete that within my allowed steps. "
            "Let me know how you'd like me to adjust."
        )
    return {
        "answer": answer,
        "loop_terminated": "max_turns",
        "memory_updated": True,
        "turns": turn,
        "reasoning_effort": reasoning_effort,
        "reasoning_trace": (
            "\n\n".join(reasoning_trace) if reasoning_trace else None
        ),
    }