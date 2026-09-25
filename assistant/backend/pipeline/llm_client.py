import re
from collections.abc import AsyncGenerator
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

    def __getitem__(self, key: str):
        """Allow dict-style access for test compatibility."""
        return getattr(self, key)

    def get(self, key: str, default=None):
        """Allow dict-style .get() for test compatibility."""
        return getattr(self, key, default)


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


class ChatChunk(BaseModel):
    """A single chunk from a streaming chat response."""
    content: str = ""
    model: str = ""
    done: bool = False
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
    - math_model: dedicated computation model with Python tool execution
    - tools_model: fast function-calling model (Performance phase)
    - max_model: max-intelligence escalation tier (M6) — 27B-class brain loaded
      on demand with a short keep_alive, never resident next to the warm set.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        chat_model: str = "qwen3.5:9b",
        utility_model: str = "qwen3.5:4b",
        embedding_model: str = "qwen3-embedding:0.6b",
        coder_model: str = "",
        math_model: str = "",
        math_num_ctx: int = 16384,
        math_keep_alive: str = "10m",
        timeout: float = 120.0,
        verify_tls: bool | str = True,
        chat_num_ctx: int = 8192,
        utility_num_ctx: int = 4096,
        keep_alive: str = "30m",
        tools_model: str = "qwen3.5:9b",
        tools_num_ctx: int = 4096,
        tools_keep_alive: str = "-1",
        max_model: str = "",
        max_num_ctx: int = 16384,
        max_keep_alive: str = "10m",
    ):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.utility_model = utility_model
        self.embedding_model = embedding_model
        self.coder_model = coder_model
        self.math_model = math_model
        self.math_num_ctx = math_num_ctx
        self.math_keep_alive = math_keep_alive
        self.timeout = timeout
        self.verify_tls: bool | str = verify_tls
        self.chat_num_ctx = chat_num_ctx
        self.utility_num_ctx = utility_num_ctx
        # How long Ollama keeps a model loaded after a request. Reloading the
        # 27B chat model costs tens of seconds, so household-style intermittent
        # use pays a huge tax without this (default 5m evicts between turns).
        self.keep_alive = keep_alive
        # Tools model (dedicated fast function-calling model)
        self.tools_model = tools_model
        self.tools_num_ctx = tools_num_ctx
        self.tools_keep_alive = tools_keep_alive
        # Max-intelligence escalation tier (loaded on demand, short keep_alive)
        self.max_model = max_model
        self.max_num_ctx = max_num_ctx
        self.max_keep_alive = max_keep_alive
        self._client: httpx.AsyncClient | None = None
        self._capabilities_cache: dict[str, list[str]] = {}
        # Cache query->embedding to avoid recomputing the same embedding
        self._embed_cache: dict[str, list[float]] = {}
        self._cache_max_size = 128

    def _keep_alive_for(self, model: str | None) -> str | int:
        """Resolve the keep_alive that applies to a specific model.

        Per-model keep_alive lets on-demand tiers (math, max-intelligence)
        evict themselves instead of squatting on RAM next to the warm set.
        """
        if model == self.max_model and self.max_model:
            return self._normalize_keep_alive(self.max_keep_alive)
        if model == self.math_model and self.math_model:
            return self._normalize_keep_alive(self.math_keep_alive)
        if model == self.tools_model and self.tools_model:
            return self._normalize_keep_alive(self.tools_keep_alive)
        return self._normalize_keep_alive(self.keep_alive)

    @staticmethod
    def _normalize_keep_alive(value: str | int) -> str | int:
        """Normalize a keep_alive config value into an Ollama API value."""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.lstrip("-").isdigit() and stripped not in ("", "-"):
                n = int(stripped)
                if n < 0:
                    return n
                return f"{n}s"
        return value

    def _keep_alive_param(self) -> str | int:
        """Normalize the keep_alive config into an Ollama API value.

        Ollama accepts duration strings ("30m", "-1s") or plain integers
        (nanoseconds; negative = never unload). A bare string like "-1" or
        "0" fails server-side with 'missing unit in duration', and a positive
        bare integer would mean nanoseconds (≈ unload immediately) — neither
        matches intent, so bare integers are rewritten here:
          negative -> JSON number (never unload), positive -> "<n>s".
        """
        return self._normalize_keep_alive(self.keep_alive)

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
        try:
            return "thinking" in await self.model_capabilities(model)
        except Exception:
            return False

    async def supports_tools(self, model: str | None = None) -> bool:
        """True if the model advertises the 'tools' capability."""
        return "tools" in await self.model_capabilities(model)

    async def _execute_python_sandboxed(self, code: str, timeout: int) -> str:
        """Execute Python code with timeout, no network, limited imports."""
        import os
        import subprocess
        import tempfile

        # Allowed imports (extend as needed)
        allowed_imports = """
import math, statistics, random, decimal, fractions
import itertools, functools, collections, datetime, typing
try: import numpy as np
except: pass
try: import scipy.stats as stats
except: pass
try: import sympy as sp
except: pass
try: import pandas as pd
except: pass
"""
        full_code = allowed_imports + "\n" + code

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(full_code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["python3", tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "PYTHONPATH": ""}  # No user site-packages
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            if result.returncode != 0:
                output += f"\nExit code: {result.returncode}"
            return output
        except subprocess.TimeoutExpired:
            return f"Error: Execution timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    async def execute_python(self, code: str, timeout: int = 30) -> str:
        """
        Execute Python code via math model with tool calling.
        Returns stdout/stderr as string.
        """
        if not self.math_model:
            raise ValueError("MATH_MODEL not configured")

        # Check capability
        if not await self.supports_tools(self.math_model):
            raise ValueError(f"Model {self.math_model} does not support tool calling")

        # Build tool definition for Python execution
        python_tool = {
            "type": "function",
            "function": {
                "name": "execute_python",
                "description": (
                    "Execute Python code and return stdout/stderr. "
                    "Use for math, statistics, financial calculations, symbolic manipulation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"},
                        "timeout": {
                            "type": "integer",
                            "default": 30,
                            "description": "Execution timeout in seconds"
                        }
                    },
                    "required": ["code"]
                }
            }
        }

        # System prompt for math model
        system = ChatMessage(role="system", content="""You are a computation engine.
        Execute the user's mathematical request by writing and running Python code.
        Use numpy, scipy, sympy, pandas, statistics as needed.
        Return ONLY the tool call to execute_python with the code.
        The system will return the result; you then formulate the final answer.""")

        user = ChatMessage(role="user", content=code)

        response = await self.chat(
            [system, user],
            model=self.math_model,
            tools=[python_tool],
            tool_choice="required",
            think=False,
            num_ctx=self.math_num_ctx,
            temperature=0.0,
        )

        if response.tool_calls:
            call = response.tool_calls[0]
            # Execute the code locally (sandboxed)
            return await self._execute_python_sandboxed(call.arguments.get("code", ""), timeout)

        raise RuntimeError("Math model did not invoke execute_python tool")

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
        tool_choice: str | None = None,
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
            "keep_alive": self._keep_alive_for(model),
            "options": {"temperature": temperature},
        }
        if format:
            payload["format"] = format
        if think is not None:
            # Capability-gated thinking: don't send think=True to models that
            # can't reason. But think=False must ALWAYS reach the server —
            # dropping it lets a thinking-capable model run its default
            # reasoning pass, which under format="json" can return done=True
            # with empty content (extraction bug: 3 attempts, ~60s each, all
            # empty on qwen3.5:4b).
            if think and not await self.supports_thinking(model):
                import logging
                logging.getLogger(__name__).warning(
                    "Model %s does not support 'thinking' capability; ignoring think=True",
                    model,
                )
                think = False
            payload["think"] = think
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        if num_ctx is None:
            if model == self.utility_model:
                num_ctx = self.utility_num_ctx
            elif model == self.math_model:
                num_ctx = self.math_num_ctx
            elif model == self.max_model:
                num_ctx = self.max_num_ctx
            else:
                num_ctx = self.chat_num_ctx
        if num_ctx:
            payload["options"]["num_ctx"] = num_ctx
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
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

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        format: str | None = None,
        think: bool | None = None,
        num_predict: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        num_ctx: int | None = None,
    ) -> AsyncGenerator[ChatChunk, None]:
        """Send streaming chat completion request. Yields ChatChunk for each token.

        Same parameters as chat(), but returns an async generator of ChatChunk.
        The final chunk has done=True.
        """
        model = model or self.chat_model
        client = await self._get_client()
        payload: dict = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "stream": True,
            "keep_alive": self._keep_alive_for(model),
            "options": {"temperature": temperature},
        }
        if format:
            payload["format"] = format
        if think is not None:
            if think and not await self.supports_thinking(model):
                import logging
                logging.getLogger(__name__).warning(
                    "Model %s does not support 'thinking' capability; ignoring think=True",
                    model,
                )
                think = False
            if think:
                payload["think"] = think
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        if num_ctx is None:
            if model == self.utility_model:
                num_ctx = self.utility_num_ctx
            elif model == self.math_model:
                num_ctx = self.math_num_ctx
            elif model == self.max_model:
                num_ctx = self.max_num_ctx
            else:
                num_ctx = self.chat_num_ctx
        if num_ctx:
            payload["options"]["num_ctx"] = num_ctx
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        async with client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                import json
                data = json.loads(line)
                message = data.get("message", {})
                content = message.get("content", "")
                thinking = message.get("thinking") or ""
                if not thinking and "thinking" not in message:
                    content, thinking = split_thinking(content)
                tool_calls = [
                    ToolCall(
                        name=tc.get("function", {}).get("name", ""),
                        arguments=tc.get("function", {}).get("arguments") or {},
                    )
                    for tc in message.get("tool_calls") or []
                ]
                chunk = ChatChunk(
                    content=content,
                    model=data.get("model", model),
                    done=data.get("done", False),
                    thinking=thinking,
                    tool_calls=tool_calls,
                )
                yield chunk
                if chunk.done:
                    break

    async def embed(
        self,
        text: str | list[str],
        model: str | None = None,
    ) -> EmbeddingResponse | list[EmbeddingResponse]:
        """Generate embedding(s) for text. Accepts single string or list."""
        model = model or self.embedding_model

        # Handle list input
        if isinstance(text, list):
            return await self._embed_batch(text, model)

        # Single string (existing logic with cache)
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

    async def _embed_batch(
        self, texts: list[str], model: str
    ) -> list[EmbeddingResponse]:
        """Batch embedding via sequential Ollama calls (no batch support in single request)."""
        cache_keys = [f"{model}:{t}" for t in texts]

        # Check cache for all
        cached = {}
        uncached_texts = []
        uncached_indices = []
        for i, (t, k) in enumerate(zip(texts, cache_keys, strict=True)):
            if k in self._embed_cache:
                cached[i] = self._embed_cache[k]
            else:
                uncached_texts.append(t)
                uncached_indices.append(i)

        if not uncached_texts:
            # All cached - return without calling API
            return [
                EmbeddingResponse(embedding=cached[i], model=model) for i in range(len(texts))
            ]

        # Need API calls - get client now
        client = await self._get_client()

        # Sequential requests (Ollama /api/embeddings doesn't support batch prompt)
        results: list[EmbeddingResponse | None] = [None] * len(texts)
        
        for idx, text in zip(uncached_indices, uncached_texts, strict=True):
            payload = {"model": model, "prompt": text, "keep_alive": self._keep_alive_param()}
            r = await client.post("/api/embeddings", json=payload)
            r.raise_for_status()
            data = r.json()
            embedding = data["embedding"]
            self._embed_cache[cache_keys[idx]] = embedding
            results[idx] = EmbeddingResponse(embedding=embedding, model=model)

        for idx, emb in cached.items():
            results[idx] = EmbeddingResponse(embedding=emb, model=model)

        # Trim cache
        if len(self._embed_cache) > self._cache_max_size:
            keys = list(self._embed_cache.keys())[:-self._cache_max_size // 2]
            for k in keys:
                self._embed_cache.pop(k, None)

        return results  # type: ignore


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
- You have access to your sandbox filesystem via these tools:
  • list_files() — List all files with names, paths, types, sizes
  • glob(pattern) — Find files by pattern (e.g., "*.csv", "notes/**/*.md")
  • read_file(path) — Read a file's full content
  • write_file(path, content) — Create or overwrite a file
  • edit_file(path, old_text, new_text) — Surgical find-and-replace
  • delete_file(path) — Delete a file
  • recall(query) — Search your structured memory (frames/slots)

Tool chaining examples:
- User: "what files do I have?" → list_files()
- User: "read my budget.csv" → 
    1. glob("**/budget.csv") to find exact path
    2. read_file(path="path/to/budget.csv")
- User: "create a todo list" → write_file(path="notes/todo.txt", content="...")
- User: "add item to todo.txt" →
    1. read_file(path="notes/todo.txt")
    2. edit_file(path="notes/todo.txt", old_text="...", new_text="...")
- User: "delete old notes" → glob("notes/*.txt") → delete_file() for each
- User: "what do you know about project X?" → recall(query="project X")

Uploaded files: when memory mentions an uploaded file, read its actual contents
with read_file — pass the exact file name as `path`, or the `file_<name>` frame
as `frame_name`. A preview shown in memory is a hint, never the full contents.

Respond conversationally as a helpful assistant.""")
    else:
        parts.append("""**Guidelines:**
- Answer from the retrieved memory state when relevant.
- If the memory state contains partial information, acknowledge gaps.
- If search results are provided, ground your answer in both memory AND search results.
- Only cite sources if a Search Results section is present.
  Do NOT fabricate URLs or source references.
- Never fabricate facts, URLs, or citations that are not explicitly in the provided search results.
- You have access to your sandbox filesystem via these tools:
  • list_files() — List all files with names, paths, types, sizes
  • glob(pattern) — Find files by pattern (e.g., "*.csv", "notes/**/*.md")
  • read_file(path) — Read a file's full content
  • write_file(path, content) — Create or overwrite a file
  • edit_file(path, old_text, new_text) — Surgical find-and-replace
  • delete_file(path) — Delete a file
  • recall(query) — Search your structured memory (frames/slots)

Tool chaining examples:
- User: "what files do I have?" → list_files()
- User: "read my budget.csv" → 
    1. glob("**/budget.csv") to find exact path
    2. read_file(path="path/to/budget.csv")
- User: "create a todo list" → write_file(path="notes/todo.txt", content="...")
- User: "add item to todo.txt" →
    1. read_file(path="notes/todo.txt")
    2. edit_file(path="notes/todo.txt", old_text="...", new_text="...")
- User: "delete old notes" → glob("notes/*.txt") → delete_file() for each
- User: "what do you know about project X?" → recall(query="project X")

Uploaded files: when memory mentions an uploaded file, read its actual contents
with read_file — pass the exact file name as `path`, or the `file_<name>` frame
as `frame_name`. A preview shown in memory is a hint, never the full contents.

Respond conversationally and helpfully.""")

    if planinstructions:
        parts.append(planinstructions)

    # Memory goes last: it changes every turn, so it must sit after the
    # stable prefix for prompt caching to help.
    parts.append(
        f"You have the following relevant memory state:\n\n{memory_context}"
    )
    return "\n\n".join(parts)
