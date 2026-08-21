"""Tool framework (Phase 6 M5): native Ollama tool-calling on the chat model.

A minimal registry of household-safe, local-first tools. The chat model
decides when to call them; results are fed back until it answers in prose.
No cloud APIs — web_search goes through the local SearXNG instance only.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from assistant.backend.pipeline.llm_client import ChatMessage, ChatResponse
from assistant.backend.pipeline.search import WebSearchTool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3


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


def builtin_tools(search_tool: WebSearchTool) -> list[AssistantTool]:
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
                handler=_make_search_handler(search_tool),
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


def _make_search_handler(search_tool: WebSearchTool):
    async def handler(query: str, num_results: int = 5) -> str:
        try:
            results = await search_tool.search(query, num_results=num_results)
        except Exception as e:
            logger.warning("Tool web_search failed: %s", e)
            return f"Search failed: {e}"
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
