# Performance: Streaming + Dedicated Tools Model + Async Execution

## Overview
Fix the tool-calling timeout by implementing three targeted improvements:
1. **Streaming responses** (SSE) — perceived latency near zero
2. **Dedicated 1.5B tools model** — fast function calling for tool loop
3. **Async tool execution** — parallelize independent tool calls

All scoped to **text files only**. No multi-modal.

---

## Problem Statement

Current: Tool loop runs on `utility_model` (qwen3.5:4b) with 17 tools + 4000+ token system prompt.
- First tool call: 5-15s (cold + large prefill)
- Subsequent turns: 3-10s each
- Total: 30-60s/turn → exceeds Caddy 30s timeout
- User sees nothing until complete

Target: First token <1s, tool calls complete in <5s total.

---

## 1. Streaming Responses (SSE)

### Why
- Perceived latency → zero (user sees tokens immediately)
- Works with current models — no model change needed
- Standard UX expectation

### Implementation

**Backend: `OllamaClient.chat(stream=True)`**
```python
async def chat(self, ..., stream: bool = False) -> AsyncGenerator[ChatChunk, None] | ChatResponse:
    payload["stream"] = stream
    async with client.stream("POST", "/api/chat", json=payload) as resp:
        async for line in resp.aiter_lines():
            if line:
                yield parse_chunk(line)
```

**Orchestrator: Tool loop streaming**
```python
async def run_tool_loop_streaming(...) -> AsyncGenerator[ToolLoopEvent, None]:
    while turn < max_turns:
        # Stream tool calls + text as they arrive
        async for chunk in llm_client.chat(messages, tools=tools, stream=True):
            if chunk.tool_calls:
                yield ToolCallEvent(chunk.tool_calls)
                # Execute tools, yield results
            elif chunk.content:
                yield TextDeltaEvent(chunk.content)
```

**Frontend: EventSource consumer**
```javascript
const es = new EventSource('/chat/stream');
es.onmessage = (e) => {
    const event = JSON.parse(e.data);
    if (event.type === 'text_delta') append(event.delta);
    if (event.type === 'tool_call') showSpinner(event.tool);
};
```

**Episode logging**: Buffer full response in memory, log at completion.

### Scope
- Stream final answer only (tool calls remain synchronous for now)
- Phase 2: stream tool calls too

---

## 2. Dedicated 1.5B Tools Model

### Why
- 4B model still too slow for 17-tool schema + large context
- 1.5B function-calling models (qwen2.5-coder:1.5b, smollm2:1.7b) do tool calling in ~500ms
- Separate keep-alive: tools model stays warm independently
- Low VRAM: fits alongside 7B chat + 4B utility (OLLAMA_MAX_LOADED_MODELS=3)

### Config
```yaml
# .env
TOOLS_MODEL=qwen2.5-coder:1.5b
TOOLS_NUM_CTX=4096
TOOLS_KEEP_ALIVE=-1
```

### Orchestration
```python
# Tool loop uses tools_model
tool_result = await run_tool_loop(
    llm_client,
    messages,
    tools,
    model=settings.tools_model,  # 1.5B
)

# Final answer uses chat_model (streaming)
async for chunk in llm_client.chat(messages + tool_results, stream=True):
    yield chunk
```

### Tool Set Reduction
Only give tools model what it actually calls:
```python
TOOLS_FOR_TOOL_MODEL = [
    "write_file", "read_file", "edit_file", "delete_file", "glob", "list_files",
    "recall", "upsert_slot", "upsert_association", "think", "finalize"
]
# Exclude: web_search, fetch_url, compute, run_scheduled_task, plan, search_episodes
```

---

## 3. Async Tool Execution

### Why
Currently sequential: tool A → wait → tool B → wait → tool C
Many calls are independent (e.g., `glob` + `recall`, or multiple `read_file`)

### Implementation
```python
# In run_tool_loop: batch independent calls
tool_calls = response.tool_calls
independent_groups = find_independent_groups(tool_calls)

for group in independent_groups:
    results = await asyncio.gather(*[
        execute_tool(call.name, call.arguments, ...) for call in group
    ])
    # Add all results to messages
```

### Dependency Detection
- `read_file` after `glob` → depends
- `edit_file` after `read_file` → depends
- Multiple `read_file` → independent
- `recall` + `glob` → independent

---

## Not Doing (Explicit)

| Item | Reason |
|------|--------|
| **Multi-modal (images/PDF/audio)** | Out of scope — text files only |
| **Strip memory from tool loop** | Model needs context to choose tools correctly; stripping causes hallucinated tool calls |
| **Over-engineered dependency graph** | Simple heuristic: group by tool type, sequential within type |
| **Separate planner model** | `plan` tool already works; keep simple |

---

## Implementation Order

| Phase | Work | Dependencies |
|-------|------|--------------|
| **1** | Streaming final answer (SSE) | None |
| **2** | Dedicated `TOOLS_MODEL` config + reduced tool set | Phase 1 |
| **3** | Async tool execution (parallel independent calls) | Phase 2 |
| **4** | Stream tool calls too (full streaming) | Phase 1 + 3 |

---

## Config Additions

```python
# config.py
tools_model: str = "qwen2.5-coder:1.5b"      # NEW
tools_num_ctx: int = 4096                     # NEW
tools_keep_alive: str = "-1"                  # NEW
streaming_enabled: bool = True                # NEW
```

```env
# .env.example
TOOLS_MODEL=qwen2.5-coder:1.5b
TOOLS_NUM_CTX=4096
TOOLS_KEEP_ALIVE=-1
# Streaming is ON by default — no env var needed
```

---

## Success Criteria

| Metric | Target |
|--------|--------|
| First token latency | < 1s (streaming) |
| Tool loop total time | < 5s (1.5B model + async) |
| Caddy timeout | No 504s (response_header_timeout 300s) |
| Tool call success rate | > 95% (model reliability) |
| Memory usage | < 8GB VRAM (7B + 4B + 1.5B) |

---

## File Changes

### New
- `assistant/backend/pipeline/streaming.py` — SSE helpers, chunk parsing
- `assistant/backend/pipeline/async_tools.py` — dependency detection, parallel execution

### Modified
- `config.py` — new settings
- `llm_client.py` — `chat(stream=True)` async generator
- `tools.py` — `run_tool_loop_streaming`, reduced tool set for tools_model
- `orchestrator.py` — wire streaming + tools_model
- `main.py` — init tools_model client
- `.env.example` — document new vars

### Tests
- Streaming chunk parsing
- Async tool execution correctness (order preserved for deps)
- Tools model tool calling accuracy