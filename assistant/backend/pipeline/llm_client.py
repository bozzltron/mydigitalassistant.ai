import re
from typing import Literal

import httpx
from pydantic import BaseModel

_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>(.*)$", re.DOTALL)


def split_thinking(content: str) -> tuple[str, str]:
    """Separate inline ``<think>`` blocks from the visible answer.

    Returns ``(clean_content, thinking)``. Handles multiple blocks, an unclosed
    trailing block (model cut off mid-think), and content without any tags.
    """
    if "<think>" not in content:
        return content, ""

    thinking_parts: list[str] = []

    def _collect(m: re.Match[str]) -> str:
        thinking_parts.append(m.group(1).strip())
        return ""

    clean = _THINK_BLOCK_RE.sub(_collect, content)

    # Unclosed block: everything after a leftover <think> is reasoning.
    if "<think>" in clean:
        m = _THINK_OPEN_RE.search(clean)
        if m:
            thinking_parts.append(m.group(1).strip())
        clean = clean[: clean.index("<think>")]

    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, "\n\n".join(p for p in thinking_parts if p)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None  # tool name on role="tool" messages
    # Echoed assistant turn when the model requested tool calls; passed
    # through verbatim in payloads, never parsed back.
    tool_calls: list[dict] | None = None


class ToolCall(BaseModel):
    """A tool invocation requested by the model (Ollama native tools API)."""
    name: str
    arguments: dict = {}


class ChatResponse(BaseModel):
    content: str
    model: str
    done: bool
    thinking: str = ""
    tool_calls: list[ToolCall] = []


class EmbeddingResponse(BaseModel):
    embedding: list[float]
    model: str


class OllamaClient:
    """Async client for local Ollama server. No external calls.

    Model roles (Phase 6 fleet):
    - chat_model: user-facing responses; thinking-capable models accept think=True
    - utility_model: extraction + task-routing fallback
    - embedding_model: frame/query embeddings
    - coder_model: reserved for tool codegen (M5); empty = fall back to chat_model
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        chat_model: str = "qwen2.5:7b",
        utility_model: str = "qwen2.5:3b",
        embedding_model: str = "nomic-embed-text",
        coder_model: str = "",
        timeout: float = 120.0,
        verify_tls: bool | str = True,
        chat_num_ctx: int = 8192,
        utility_num_ctx: int = 4096,
        keep_alive: str = "30m",
    ):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.utility_model = utility_model
        self.embedding_model = embedding_model
        self.coder_model = coder_model
        self.timeout = timeout
        self.verify_tls: bool | str = verify_tls
        self.chat_num_ctx = chat_num_ctx
        self.utility_num_ctx = utility_num_ctx
        # How long Ollama keeps a model loaded after a request. Reloading the
        # 27B chat model costs tens of seconds, so household-style intermittent
        # use pays a huge tax without this (default 5m evicts between turns).
        self.keep_alive = keep_alive
        self._client: httpx.AsyncClient | None = None
        self._capabilities_cache: dict[str, list[str]] = {}
        # Cache query->embedding to avoid recomputing the same embedding
        self._embed_cache: dict[str, list[float]] = {}
        self._cache_max_size = 128

    def _keep_alive_param(self) -> str | int:
        """Normalize the keep_alive config into an Ollama API value.

        Ollama accepts duration strings ("30m", "-1s") or plain integers
        (nanoseconds; negative = never unload). A bare string like "-1" or
        "0" fails server-side with 'missing unit in duration', and a positive
        bare integer would mean nanoseconds (≈ unload immediately) — neither
        matches intent, so bare integers are rewritten here:
          negative -> JSON number (never unload), positive -> "<n>s".
        """
        value = self.keep_alive
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.lstrip("-").isdigit() and stripped not in ("", "-"):
                n = int(stripped)
                if n < 0:
                    return n
                return f"{n}s"
        return value

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OllamaClient":
        await self._get_client()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def health_check(self) -> bool:
        """Returns True if Ollama is reachable."""
        try:
            client = await self._get_client()
            r = await client.get("/api/tags")
            return r.status_code == 200
        except Exception:
            return False

    async def model_capabilities(self, model: str | None = None) -> list[str]:
        """Return capability labels (e.g. ["thinking", "tools"]) for a model.

        Results are cached per model. Empty list if the probe fails.
        """
        model = model or self.chat_model
        if model in self._capabilities_cache:
            return self._capabilities_cache[model]
        try:
            client = await self._get_client()
            r = await client.post("/api/show", json={"model": model})
            r.raise_for_status()
            caps = list(r.json().get("capabilities", []))
        except Exception:
            caps = []
        self._capabilities_cache[model] = caps
        return caps

    async def supports_thinking(self, model: str | None = None) -> bool:
        """True if the model advertises the 'thinking' capability."""
        return "thinking" in await self.model_capabilities(model)

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        format: str | None = None,  # "json" for structured output
        stream: bool = False,
        think: bool | None = None,
        num_predict: int | None = None,
        tools: list[dict] | None = None,
        num_ctx: int | None = None,
    ) -> ChatResponse:
        """Send chat completion request. Uses chat_model by default.

        think: for thinking-capable models, request/suppress a reasoning chain
        via Ollama's per-request flag. None leaves the server default in charge.
        tools: Ollama native tools API — list of {"type": "function",
        "function": {name, description, parameters}} defs. Requested calls come
        back on ChatResponse.tool_calls.
        num_ctx: context window override. None auto-selects by role — the
        utility model gets utility_num_ctx, everything else chat_num_ctx
        (plan §4.4: never let Ollama's 32K default inflate KV allocation).
        """
        model = model or self.chat_model
        client = await self._get_client()
        payload: dict = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "stream": stream,
            "keep_alive": self._keep_alive_param(),
            "options": {"temperature": temperature},
        }
        if format:
            payload["format"] = format
        if think is not None:
            payload["think"] = think
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        if num_ctx is None:
            num_ctx = (
                self.utility_num_ctx if model == self.utility_model else self.chat_num_ctx
            )
        if num_ctx:
            payload["options"]["num_ctx"] = num_ctx
        if tools:
            payload["tools"] = tools
        r = await client.post("/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        content = data["message"]["content"]
        # Structured field when available; otherwise parse inline <think> tags.
        thinking = data["message"].get("thinking") or ""
        if not thinking:
            content, thinking = split_thinking(content)
        tool_calls = [
            ToolCall(
                name=tc.get("function", {}).get("name", ""),
                arguments=tc.get("function", {}).get("arguments") or {},
            )
            for tc in data["message"].get("tool_calls") or []
        ]
        return ChatResponse(
            content=content,
            model=data["model"],
            done=data.get("done", True),
            thinking=thinking,
            tool_calls=tool_calls,
        )

    async def embed(
        self,
        text: str,
        model: str | None = None,
    ) -> EmbeddingResponse:
        """Generate embedding for text. Uses embedding_model by default."""
        model = model or self.embedding_model
        cache_key = f"{model}:{text}"
        if cache_key in self._embed_cache:
            cached = self._embed_cache[cache_key]
            # Trim cache if it grows too large
            if len(self._embed_cache) > self._cache_max_size:
                # Remove oldest entries (simple FIFO-like trim)
                keys = list(self._embed_cache.keys())[:-self._cache_max_size // 2]
                for k in keys:
                    self._embed_cache.pop(k, None)
            return EmbeddingResponse(embedding=cached, model=model)

        client = await self._get_client()
        payload = {"model": model, "prompt": text, "keep_alive": self._keep_alive_param()}
        r = await client.post("/api/embeddings", json=payload)
        r.raise_for_status()
        data = r.json()
        embedding = data["embedding"]
        # Cache the result (evict if needed)
        self._embed_cache[cache_key] = embedding
        if len(self._embed_cache) > self._cache_max_size:
            keys = list(self._embed_cache.keys())[:-self._cache_max_size // 2]
            for k in keys:
                self._embed_cache.pop(k, None)
        return EmbeddingResponse(embedding=embedding, model=model)


def build_system_prompt(
    memory_context: str,
    task_type: str,    # "functional" | "introspective"
    planinstructions: str = "",
    self_context: str = "",
) -> str:
    """Build a system prompt that injects structured memory context.

    Sections are ordered stable-first, volatile-last: persona and task
    guidance rarely change between turns, while memory state changes every
    turn. Keeping the volatile material at the end lets Ollama's prompt
    cache reuse the prefill of the stable prefix on consecutive turns,
    which saves tens of seconds per turn on large local models.

    planinstructions: additional instructions from the reasoner's Plan,
    e.g. citation requirements, memory-sufficiency caveats, search directives.
    self_context: the agent's own identity facts (name, working agreements),
    always included when available so responses stay consistent with them.
    """
    if self_context:
        persona = f"""You are a personal cognitive assistant with a structured memory system.

Who you are (from your own memory — treat as always true):
{self_context}

Match reply length and structure to the question: answer short factual
questions briefly in plain prose; reserve lists, headers, and tables for
answers that genuinely need them."""
    else:
        persona = """You are a cognitive digital assistant with a structured memory system.

Match reply length and structure to the question: answer short factual
questions briefly in plain prose; reserve lists, headers, and tables for
answers that genuinely need them."""

    parts: list[str] = [persona]
    if task_type == "introspective":
        parts.append("""
For this query, the user is asking about YOUR memory or knowledge. You must:
1. Ground your answer ONLY in the retrieved memory state below.
2. Cite specific frames/slots/episodes when relevant (frame IDs if available).
3. If the memory doesn't contain the answer, say so clearly — do not hallucinate.
4. Be honest about uncertainty (low-confidence slots).
5. For introspective queries, prefer citing episode content over slot values.

Respond conversationally as a helpful assistant.""")
    else:
        parts.append("""**Guidelines:**
- Answer from the retrieved memory state when relevant.
- If the memory state contains partial information, acknowledge gaps.
- If search results are provided, ground your answer in both memory AND search results.
- Only cite sources if a Search Results section is present.
  Do NOT fabricate URLs or source references.
- Never fabricate facts, URLs, or citations that are not explicitly in the provided search results.

Respond conversationally and helpfully.""")

    if planinstructions:
        parts.append(planinstructions)

    # Memory goes last: it changes every turn, so it must sit after the
    # stable prefix for prompt caching to help.
    parts.append(
        f"You have the following relevant memory state:\n\n{memory_context}"
    )
    return "\n\n".join(parts)
