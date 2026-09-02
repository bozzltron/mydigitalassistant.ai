"""Tool framework (Phase 6 M5): native Ollama tool-calling on the chat model.

A minimal registry of household-safe, local-first tools. The chat model
decides when to call them; results are fed back until it answers in prose.
No cloud APIs — web_search goes through the local SearXNG instance only.
"""

import asyncio
import json
import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, ChatResponse
from assistant.backend.pipeline.search import WebSearchTool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3
MAX_FETCH_BYTES = 500_000
FETCH_TIMEOUT_SECONDS = 15


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


def _current_datetime() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %A (%Z)")


async def _handle_datetime(**_: object) -> str:
    return _current_datetime()


async def _check_robots_txt(client: httpx.AsyncClient, url: str) -> bool:
    """Return True if fetching url is allowed by robots.txt, False if blocked.

    Robots.txt spec: first matching user-agent block wins. Specific agent
    matches take priority over the generic '*' block.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        r = await client.get(robots_url, timeout=5.0)
        if r.status_code != 200:
            return True

        url_path = parsed.path or "/"

        def first_match(path_rule: str) -> bool:
            if not path_rule:
                return False
            if path_rule == "/":
                return True
            if path_rule.endswith("$"):
                return url_path == path_rule[:-1]
            return url_path.startswith(path_rule)

        specific_allowed: bool | None = None
        general_allowed: bool | None = None
        in_specific_block = False

        for line in r.text.splitlines():
            lower = line.lower().strip()
            if not lower or lower.startswith("#"):
                continue
            if lower.startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip()
                if _matches_agent(agent, "AssistantBot"):
                    in_specific_block = True
                    specific_allowed = None
                else:
                    in_specific_block = False
                    if specific_allowed is None:
                        if agent == "*":
                            general_allowed = None
                        else:
                            if general_allowed is None:
                                general_allowed = None
            elif in_specific_block and specific_allowed is None:
                if lower.startswith("allow:"):
                    path = lower.split(":", 1)[1].strip()
                    if first_match(path):
                        specific_allowed = True
                elif lower.startswith("disallow:"):
                    path = lower.split(":", 1)[1].strip()
                    if first_match(path):
                        specific_allowed = False
            elif not in_specific_block and general_allowed is None:
                if lower.startswith("allow:"):
                    path = lower.split(":", 1)[1].strip()
                    if first_match(path):
                        general_allowed = True
                elif lower.startswith("disallow:"):
                    path = lower.split(":", 1)[1].strip()
                    if first_match(path):
                        general_allowed = False

        if specific_allowed is not None:
            return specific_allowed
        if general_allowed is not None:
            return general_allowed
        return True
    except Exception:
        return True


def _matches_agent(rule: str, target: str) -> bool:
    return target.lower() in rule.lower() or rule.lower() == "*"


def _url_path_matches(url: str, rule: str) -> bool:
    parsed_path = urllib.parse.urlparse(url).path or "/"
    if not rule:
        return False
    if rule == "/":
        return True
    if rule.endswith("$"):
        return parsed_path == rule[:-1]
    return parsed_path.startswith(rule)


async def _fetch_single_url(url: str) -> str:
    """Fetch a URL, strip HTML, return plain text. No external calls."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Error: only http/https URLs are supported, got {parsed.scheme!r}"
    except Exception as e:
        return f"Error: malformed URL {e}"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS, read=20.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AssistantBot/1.0)"},
        ) as client:
            allowed = await _check_robots_txt(client, url)
            if not allowed:
                return f"Error: {url} is blocked by robots.txt"

            r = await client.get(url)
            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return r.text[:2000]

            raw = r.content[:MAX_FETCH_BYTES]
            try:
                raw = raw.decode(r.encoding or "utf-8", errors="replace")
            except Exception:
                raw = raw.decode("utf-8", errors="replace")

            text = _strip_html(raw)

            if not text.strip():
                return (
                    "Error: page appears to be JavaScript-rendered "
                    "(empty after HTML strip). Try searching for the page "
                    "content instead."
                )

            snippet = text[:3000]
            if len(text) > 3000:
                snippet += f"\n... [{len(text):,} total characters, truncated to first 3000]"
            return snippet

    except httpx.TimeoutException:
        return f"Error: timeout fetching {url} ({FETCH_TIMEOUT_SECONDS}s)"
    except Exception as e:
        return f"Error fetching {url}: {e}"


def _make_fetch_url_handler(
    store=None,
    llm_client=None,
):
    async def handler(url: str) -> str:
        content = await _fetch_single_url(url)
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


def builtin_tools(
    search_tool: WebSearchTool,
    store=None,
    llm_client=None,
    embed_fn=None,
) -> list[AssistantTool]:
    """Build the default toolset. Search is gated on SearXNG availability."""
    tools: list[AssistantTool] = [
        AssistantTool(
            name="get_current_datetime",
            description=(
                "Get the current local date and time. Use before answering any "
                "question about 'today', 'tomorrow', or computing date offsets."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_handle_datetime,
        ),
        AssistantTool(
            name="calculate",
            description=(
                "Evaluate an arithmetic expression. Use for any math beyond "
                "trivial mental math. Supports + - * / ** % and parentheses."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Arithmetic expression, e.g. '(2+3)*7'",
                    },
                },
                "required": ["expression"],
            },
            handler=_handle_calculate,
        ),
        AssistantTool(
            name="fetch_url",
            description=(
                "Fetch the full text content of a URL. Use when the user provides "
                "a specific URL to read. Strips HTML to plain text. Returns "
                "up to 500KB of content. Use this to read pages that search "
                "cannot reach or that have not been indexed yet."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch (must be http or https)",
                    },
                },
                "required": ["url"],
            },
            handler=_make_fetch_url_handler(store=store, llm_client=llm_client),
        ),
    ]
    if search_tool.enabled:
        tools.append(
            AssistantTool(
                name="web_search",
                description=(
                    "Search the web for current information via the private "
                    "local meta-search engine. Use when the answer needs facts "
                    "you do not already know and memory is insufficient."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "num_results": {
                            "type": "integer",
                            "description": "How many results (default 5)",
                        },
                    },
                    "required": ["query"],
                },
                handler=_make_search_handler(search_tool, embed_fn=embed_fn),
            )
        )
    # File tools are always available (local file operations only)
    tools.extend([
        AssistantTool(
            name="file_lookup",
            description=(
                "Search for files by name pattern and optional file type. "
                "Use when the user refers to 'the file called X' or 'the ical file'. "
                "Returns matching files with name, extension, size, and content preview."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name_pattern": {
                        "type": "string",
                        "description": "Name pattern or keyword to match against file names",
                    },
                    "file_type": {
                        "type": "string",
                        "description": "Optional file extension filter (e.g. 'ics', 'csv', 'json')",
                    },
                },
                "required": ["name_pattern"],
            },
            handler=_handle_file_lookup,
        ),
        AssistantTool(
            name="file_read",
            description=(
                "Read the full content of a file by its frame ID. Use after file_lookup "
                "to get the ID of a file the user is referencing. Returns stored metadata "
                "and actual file content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "integer",
                        "description": "The frame ID of the file to read",
                    },
                },
                "required": ["file_id"],
            },
            handler=_handle_file_read,
        ),
        AssistantTool(
            name="file_write",
            description=(
                "Create a new file and store it in memory. Provide a name, content, and "
                "file type (extension). The file will be saved to the data directory and "
                "a frame will be created with metadata slots. Use when the user says "
                "'create a new file called X' or 'write Y to file Z'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the file (without extension)",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write to the file",
                    },
                    "file_type": {
                        "type": "string",
                        "description": "File extension without dot (e.g. 'ics', 'csv', 'txt')",
                    },
                },
                "required": ["name", "content", "file_type"],
            },
            handler=_handle_file_write,
        ),
        AssistantTool(
            name="file_update",
            description=(
                "Update the content of an existing file by its frame ID. Use after "
                "file_lookup to find the file, then provide new content. The file in the "
                "data directory will be overwritten and memory slots updated."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "integer",
                        "description": "The frame ID of the file to update",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The new content to write to the file",
                    },
                },
                "required": ["file_id", "new_content"],
            },
            handler=_handle_file_update,
        ),
        AssistantTool(
            name="file_delete",
            description=(
                "Delete a file by its frame ID. Use after file_lookup to find the file "
                "the user wants to remove. The file in the data directory will be removed "
                "and the memory frame deleted."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "integer",
                        "description": "The frame ID of the file to delete",
                    },
                },
                "required": ["file_id"],
            },
            handler=_handle_file_delete,
        ),
        AssistantTool(
            name="file_search",
            description=(
                "Search file content for a query term. Use when the user wants to find "
                "specific information inside their files. Returns matching files with "
                "snippets showing where the term appears."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The text term to search for inside file contents",
                    },
                    "file_type": {
                        "type": "string",
                        "description": "Optional file extension filter (e.g. 'ics', 'csv')",
                    },
                },
                "required": ["query"],
            },
            handler=_handle_file_search,
        ),
    ])

    return tools


async def _handle_calculate(expression: str) -> str:
    allowed = set("0123456789+-*/%(). ")
    if not expression or set(expression) - allowed:
        return f"Error: expression contains disallowed characters: {expression!r}"
    try:
        result = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
    except Exception as e:
        return f"Error evaluating {expression!r}: {e}"
    return f"{expression} = {result}"


def _make_search_handler(search_tool: WebSearchTool, embed_fn=None):
    from assistant.backend.pipeline.search import filter_relevant

    async def handler(query: str, num_results: int = 5) -> str:
        try:
            results = await search_tool.search(query, num_results=num_results)
        except Exception as e:
            logger.warning("Tool web_search failed: %s", e)
            return f"Search failed: {e}"

        if embed_fn is not None:
            results = await filter_relevant(results, query, embed_fn)

        if not results:
            return "No results found."
        return "\n".join(
            f"- [{r.title}]({r.url}) {r.snippet}" for r in results if r.url or r.title
        )

    return handler


async def run_tool_loop(
    llm_client,
    messages: list[ChatMessage],
    tools: list[AssistantTool],
    *,
    think: bool | None = None,
    num_predict: int | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> ChatResponse:
    """Run chat-with-tools until the model answers in plain prose.

    Returns the final ChatResponse (thinking accumulated across rounds).
    Tool calls are executed locally; each result is appended as a role=tool
    message. Bounded by max_rounds so a misbehaving model cannot loop forever.
    """
    defs = [t.to_def() for t in tools]
    by_name = {t.name: t for t in tools}
    convo = list(messages)
    thinking = ""
    response: ChatResponse | None = None

    for _ in range(max_rounds):
        response = await llm_client.chat(
            convo,
            think=think,
            num_predict=num_predict,
            tools=defs,
        )
        if response.thinking:
            # Preserve latest reasoning chain for audit; never feed back (§6.3).
            thinking = response.thinking
        if not response.tool_calls:
            response.thinking = thinking
            return response

        convo.append(
            ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=[
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in response.tool_calls
                ],
            )
        )
        for tc in response.tool_calls:
            tool = by_name.get(tc.name)
            if tool is None:
                result = f"Error: unknown tool {tc.name!r}"
            else:
                try:
                    result = await tool.handler(**tc.arguments)
                except TypeError as e:
                    result = f"Error: bad arguments for {tc.name}: {e}"
                except Exception as e:
                    logger.exception("Tool %s failed", tc.name)
                    result = f"Error running {tc.name}: {e}"
            convo.append(ChatMessage(role="tool", content=str(result), name=tc.name))

    logger.warning("Tool loop hit max rounds (%d); answering without tools", max_rounds)
    final = await llm_client.chat(convo, think=think, num_predict=num_predict)
    final.thinking = final.thinking or thinking
    return final


# ──────────────────────────────────────────────────────────────────────
# File Tools
# ──────────────────────────────────────────────────────────────────────

async def _handle_file_lookup(name_pattern: str, file_type: str | None = None) -> str:
    """Search for files matching a name pattern and optional file type.

    Returns a JSON summary of matching files.
    """
    from assistant.backend.main import get_store

    store: MemoryStore = get_store()
    # Search frames with "file_" prefix and matching pattern
    all_frames = await store.list_frames()
    matches = []
    for frame in all_frames:
        frame_name = frame.name
        if not frame_name.startswith("file_"):
            continue
        # Extract the original filename from the slot
        file_name_slot = await store.get_slot(frame.id, "file_name")
        file_ext_slot = await store.get_slot(frame.id, "file_ext")
        if not file_name_slot or not file_ext_slot:
            continue
        file_name_val = (
            file_name_slot.value if hasattr(file_name_slot, 'value') else str(file_name_slot)
        )
        file_ext_val = (
            file_ext_slot.value if hasattr(file_ext_slot, 'value') else str(file_ext_slot)
        )
        file_name = file_name_val
        file_ext = file_ext_val
        # Match pattern
        pattern_match = True
        if name_pattern and name_pattern.lower() not in file_name.lower():
            pattern_match = False
        if file_type and file_type != file_ext:
            pattern_match = False
        if pattern_match:
            # Read content preview
            content_slot = await store.get_slot(frame.id, "file_content_preview")
            content = content_slot.value if content_slot else ""
            matches.append({
                "file_name": file_name,
                "file_ext": file_ext,
                "file_size": await _get_slot_value(frame.id, "file_size"),
                "content_preview": content[:100] if content else "",
            })
    return json.dumps(matches)


async def _handle_file_read(file_id: int) -> str:
    """Read the full content of a file by its frame ID.

    Returns the stored content or an error message.
    """
    from assistant.backend.main import get_store

    store: MemoryStore = get_store()
    frame = await store.get_frame_by_id(file_id)
    if not frame:
        return f"Error: File frame {file_id} not found"

    # Get the content from slots
    content_slot = await store.get_slot(frame.id, "file_content_preview")
    name_slot = await store.get_slot(frame.id, "file_name")

    content = content_slot.value if content_slot else "(no content stored)"

    # Try to read the actual file from data directory
    from pathlib import Path
    data_dir = Path("/app/data")
    # Look for the file - try various names
    actual_content = ""
    if data_dir.exists():
        # Try to find matching file
        for fp in data_dir.iterdir():
            if fp.is_file():
                fp_ext = fp.suffix.lstrip(".").lower()
                if (fp_ext == (
                    name_slot.value.rsplit(".", 1)[-1] if "." in name_slot.value else ""
                ) and name_slot.value in fp.name):
                    async with httpx.AsyncClient() as client:
                        try:
                            resp = await client.get(f"file://{fp}", timeout=10)
                            actual_content = resp.text[:5000] if hasattr(resp, 'text') else ""
                        except Exception:
                            pass

    # Combine stored content with actual file content
    combined = f"{content}\n---Actual File Content (first 5000 chars)---:{actual_content}"
    return combined[:10000] if combined else ""


async def _handle_file_write(name: str, content: str, file_type: str) -> str:
    """Create a new file and store it in memory.

    Saves to data directory and creates a file frame with slots.
    """
    from assistant.backend.main import get_store

    store: MemoryStore = get_store()
    data_dir = Path("/app/data")
    data_dir.mkdir(exist_ok=True)

    # Generate safe filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = f"file_{timestamp}.{file_type}"
    file_path = data_dir / safe_name

    # Write content to file
    async with asyncio.open(file_path, 'w') as f:
        await f.write(content)

    # Create frame for this file
    frame_name = f"file_{safe_name}"
    existing_frame = await store.get_frame_by_name(frame_name)
    if not existing_frame:
        frame = await store.create_frame(
            frame_name,
            "entity",
            source_type="file_write",
            owner_user_id=1,
            source_reliability=0.7,
        )
    else:
        frame = existing_frame

    # Store file metadata as slots
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_name",
        value=name,
        essential=0,
        priority=0.5,
        source_type="file_write",
        source_reliability=0.8,
    )

    await store.upsert_slot(
        frame_id=frame.id,
        key="file_ext",
        value=file_type,
        essential=0,
        priority=0.5,
        source_type="file_write",
        source_reliability=0.8,
    )

    await store.upsert_slot(
        frame_id=frame.id,
        key="file_size",
        value=str(len(content)),
        essential=0,
        priority=0.5,
        source_type="file_write",
        source_reliability=0.8,
    )

    # Store content preview
    content_preview = content[:200] if len(content) > 200 else content
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_content_preview",
        value=content_preview,
        essential=0,
        priority=0.5,
        source_type="file_write",
        source_reliability=0.8,
    )

    # Store the full content path hint
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_path",
        value=str(file_path),
        essential=0,
        priority=0.5,
        source_type="file_write",
        source_reliability=0.8,
    )

    # Associate with any relevant existing frames mentioning the name
    # Simple: if name contains keywords, find related frames
    related = await _find_related_frames(store, name)
    if related:
        await store.associate_frames(frame_id=frame.id, related_frame_ids=[r.id for r in related])

    return json.dumps({
        "status": "created",
        "file_name": name,
        "file_ext": file_type,
        "file_path": str(file_path),
        "frame_id": frame.id,
    })


async def _handle_file_update(file_id: int, new_content: str) -> str:
    """Update an existing file's content.

    Reads the file from data directory, overwrites content, updates memory slots.
    """
    import json
    from pathlib import Path

    from assistant.backend.main import get_store
    from assistant.backend.memory.store import MemoryStore

    store: MemoryStore = get_store()
    frame = await store.get_frame_by_id(file_id)
    if not frame:
        return f"Error: File frame {file_id} not found"

    # Get current file name to locate the file
    name_slot = await store.get_slot(frame.id, "file_name")
    ext_slot = await store.get_slot(frame.id, "file_ext")

    file_name = name_slot.value if name_slot else f"file_{file_id}"
    file_ext = ext_slot.value if ext_slot else "txt"
    file_path = Path("/app/data") / f"file_{file_id}.{file_ext}"

    # Write new content to file
    try:
        async with asyncio.open(file_path, 'w') as f:
            await f.write(new_content)
    except Exception as e:
        return f"Error writing file: {e}"

    # Update slots
    content_preview = new_content[:200] if len(new_content) > 200 else new_content
    await store.upsert_slot(
        frame_id=file_id,
        key="file_content_preview",
        value=content_preview,
        essential=0,
        priority=0.8,
        source_type="file_update",
        source_reliability=0.8,
    )
    await store.upsert_slot(
        frame_id=file_id,
        key="file_size",
        value=str(len(new_content)),
        essential=0,
        priority=0.8,
        source_type="file_update",
        source_reliability=0.8,
    )

    return json.dumps({
        "status": "updated",
        "file_name": file_name,
        "file_ext": file_ext,
        "file_size": str(len(new_content)),
        "frame_id": file_id,
    })


async def _handle_file_delete(file_id: int) -> str:
    """Delete a file by its frame ID.

    Removes from data directory and memory.
    """
    import json
    from pathlib import Path

    from assistant.backend.main import get_store
    from assistant.backend.memory.store import MemoryStore

    store: MemoryStore = get_store()
    frame = await store.get_frame_by_id(file_id)
    if not frame:
        return f"Error: File frame {file_id} not found"

    # Get file name to attempt deletion
    name_slot = await store.get_slot(frame.id, "file_name")
    ext_slot = await store.get_slot(frame.id, "file_ext")
    file_name = name_slot.value if name_slot else f"file_{file_id}"

    # Attempt to delete from data directory
    data_dir = Path("/app/data")
    file_to_delete = data_dir / f"{file_name}.{ext_slot.value if ext_slot else 'txt'}"

    try:
        if file_to_delete.exists():
            file_to_delete.unlink()
    except Exception:
        pass

    # Delete the frame
    try:
        await store.delete_frame(file_id)
    except Exception:
        pass

    return json.dumps({
        "status": "deleted",
        "file_name": file_name,
        "frame_id": file_id,
    })


async def _handle_file_search(query: str, file_type: str | None = None) -> str:
    """Search file content for a query term.

    Returns matching file info with content snippets.
    """
    import json

    from assistant.backend.main import get_store
    from assistant.backend.memory.store import MemoryStore

    store: MemoryStore = get_store()
    all_frames = await store.list_frames()
    matches = []

    for frame in all_frames:
        if not frame.name.startswith("file_"):
            continue
        # Get content preview
        content_slot = await store.get_slot(frame.id, "file_content_preview")
        if not content_slot:
            continue
        content = content_slot.value or ""
        # Simple text search
        if query.lower() in content.lower():
            name_slot = await store.get_slot(frame.id, "file_name")
            ext_slot = await store.get_slot(frame.id, "file_ext")
            size_slot = await store.get_slot(frame.id, "file_size")
            matches.append({
                "file_name": name_slot.value if name_slot else f"file_{frame.id}",
                "file_ext": ext_slot.value if ext_slot else "txt",
                "file_size": size_slot.value if size_slot else "0",
                "match_snippet": _make_snippet(content, query) if query in content else "",
            })

    return json.dumps(matches)


async def _make_snippet(content: str, query: str) -> str:
    """Create a content snippet showing the query match position."""
    idx = content.lower().index(query.lower())
    start = max(0, idx - 20)
    end = min(len(content), idx + len(query) + 20)
    return content[start:end]


async def _get_slot_value(frame_id: int, key: str) -> str:
    """Helper to get a slot value from a frame."""
    from assistant.backend.main import get_store
    from assistant.backend.memory.store import MemoryStore

    store: MemoryStore = get_store()
    slot = await store.get_slot(frame_id, key)
    if slot and hasattr(slot, 'value'):
        return slot.value
    return ""


async def _find_related_frames(store: MemoryStore, name: str) -> list:
    """Find frames related to a given name/keyword."""
    all_frames = await store.list_frames()
    related = []
    for frame in all_frames:
        # Check slots for name matches
        slots = await store.list_slots(frame.id)
        for slot in slots:
            try:
                val = slot.value if hasattr(slot, 'value') else str(slot)
                if name.lower() in str(val).lower():
                    related.append(frame)
                    break
            except Exception:
                pass
    return related[:5]  # Limit to 5 related frames
