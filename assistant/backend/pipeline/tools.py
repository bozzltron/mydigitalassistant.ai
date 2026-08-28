"""Tool framework (Phase 6 M5): native Ollama tool-calling on the chat model.

A minimal registry of household-safe, local-first tools. The chat model
decides when to call them; results are fed back until it answers in prose.
No cloud APIs — web_search goes through the local SearXNG instance only.
"""

import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

import httpx

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
