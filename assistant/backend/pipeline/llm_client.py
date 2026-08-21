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
    role: Literal["system", "user", "assistant"]
    content: str


class ChatResponse(BaseModel):
    content: str
    model: str
    done: bool
    thinking: str = ""


class EmbeddingResponse(BaseModel):
    embedding: list[float]
    model: str


class OllamaClient:
    """Async client for local Ollama server. No external calls.

    Model roles (Phase 6 fleet):
    - chat_model: user-facing responses; thinking-capable models accept think=True
    - utility_model: extraction + task-routing fallback + cron generation
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
    ):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.utility_model = utility_model
        self.embedding_model = embedding_model
        self.coder_model = coder_model
        self.timeout = timeout
        self.verify_tls: bool | str = verify_tls
        self._client: httpx.AsyncClient | None = None
        self._capabilities_cache: dict[str, list[str]] = {}

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
    ) -> ChatResponse:
        """Send chat completion request. Uses chat_model by default.

        think: for thinking-capable models, request/suppress a reasoning chain
        via Ollama's per-request flag. None leaves the server default in charge.
        """
        model = model or self.chat_model
        client = await self._get_client()
        payload: dict = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if format:
            payload["format"] = format
        if think is not None:
            payload["think"] = think
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        r = await client.post("/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        content = data["message"]["content"]
        # Structured field when available; otherwise parse inline <think> tags.
        thinking = data["message"].get("thinking") or ""
        if not thinking:
            content, thinking = split_thinking(content)
        return ChatResponse(
            content=content,
            model=data["model"],
            done=data.get("done", True),
            thinking=thinking,
        )

    async def embed(
        self,
        text: str,
        model: str | None = None,
    ) -> EmbeddingResponse:
        """Generate embedding for text. Uses embedding_model by default."""
        model = model or self.embedding_model
        client = await self._get_client()
        payload = {"model": model, "prompt": text}
        r = await client.post("/api/embeddings", json=payload)
        r.raise_for_status()
        data = r.json()
        return EmbeddingResponse(
            embedding=data["embedding"],
            model=model,
        )


def build_system_prompt(
    memory_context: str,
    task_type: str,    # "functional" | "introspective"
    planinstructions: str = "",
) -> str:
    """Build a system prompt that injects structured memory context.

    planinstructions: additional instructions from the reasoner's Plan,
    e.g. citation requirements, memory-sufficiency caveats, search directives.
    """
    base = f"""You are a cognitive digital assistant with a structured memory system.

You have the following relevant memory state:

{memory_context}
"""
    if planinstructions:
        base += f"\n\n{planinstructions}\n"

    if task_type == "introspective":
        return base + """
For this query, the user is asking about YOUR memory or knowledge. You must:
1. Ground your answer ONLY in the memory state above.
2. Cite specific frames/slots/episodes when relevant (frame IDs if available).
3. If the memory doesn't contain the answer, say so clearly — do not hallucinate.
4. Be honest about uncertainty (low-confidence slots).
5. For introspective queries, prefer citing episode content over slot values.

Respond conversationally as a helpful assistant."""
    else:
        return base + """
**Guidelines:**
- Answer from the memory state above when relevant.
- If the memory state contains partial information, acknowledge gaps.
- If search results are provided, ground your answer in both memory AND search results.
- Only cite sources if the Search Results section is present above.
  Do NOT fabricate URLs or source references.
- Never fabricate facts, URLs, or citations that are not explicitly in the provided search results.

Respond conversationally and helpfully."""
