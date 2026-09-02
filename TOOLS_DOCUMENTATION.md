# Tools Documentation

## Overview

The assistant uses a native Ollama tool-calling system where the chat model decides when to call local tools, and results are fed back into the conversation context. All tools are local-first — no external/cloud APIs.

## Tool Registry

Tools are registered in `assistant/backend/pipeline/tools.py` via the `builtin_tools()` function. Each tool is an `AssistantTool` dataclass with:
- `name`: Tool name (used in model tool calls)
- `description`: Shown to the model to decide when to call it
- `parameters`: JSON Schema for the function arguments
- `handler`: Async callable that executes the tool

The tool definitions are sent to Ollama's `/api/chat` with `tools` parameter. The model returns `tool_calls`, which are executed locally via `run_tool_loop()`.

---

## Registered Tools (from `builtin_tools()`)

### 1. `get_current_datetime`
- **When to use**: Any question about "today", "tomorrow", date offsets
- **Arguments**: None (empty object `{"type": "object", "properties": {}}`)
- **Handler**: `_handle_datetime` — returns current local time in format `YYYY-MM-DD HH:MM AM/PM (TZ)`
- **Max rounds**: 1 (always answers immediately)

### 2. `calculate`
- **When to use**: Any math beyond trivial mental math
- **Arguments**: `{"expression": {"type": "string", "description": "Arithmetic expression, e.g. '(2+3)*7'"}}`
- **Handler**: `_handle_calculate` — evaluates using Python `eval()` with restricted builtins
- **Allowed chars**: `0123456789+-*/%(). `
- **Max rounds**: 1

### 3. `fetch_url`
- **When to use**: User provides a specific URL to read; search can't reach it or not indexed
- **Arguments**: `{"url": {"type": "string", "description": "The URL to fetch (must be http or https)"}}`
- **Handler**: `_make_fetch_url_handler` — fetches URL, strips HTML, auto-extracts facts to memory
- **Features**: Robots.txt check, 500KB content limit, fact extraction to memory slots
- **Max rounds**: 1 (content returned, model uses it)

### 4. `web_search`
- **When to use**: Answer needs facts you don't already know; memory is insufficient
- **Arguments**: `{"query": {"type": "string"}, "num_results": {"type": "integer", "description": "How many results (default 5)"}}`
- **Handler**: `_make_search_handler` — uses `WebSearchTool` (SearXNG primary, Brave optional)
- **Features**: Embedding-based relevance filtering (`filter_relevant`), source reliability differentiation
- **Only enabled when**: `BRAVE_ENABLED=true` + `BRAVE_API_KEY` configured, OR SearXNG running locally
- **Max rounds**: 1

### 5. `file_lookup`
- **When to use**: User refers to "the file called X" or "the ical file"; search for files by name
- **Arguments**: 
  - `{"name_pattern": {"type": "string", "description": "Name pattern or keyword to match against file names"}}`
  - `{"file_type": {"type": "string", "description": "Optional file extension filter (e.g. 'ics', 'csv', 'json')"}}`
- **Handler**: `_handle_file_lookup` — searches frames with `file_` prefix, matches against `file_name` slot
- **Returns**: JSON array of matches with `file_name`, `file_ext`, `file_size`, `content_preview`
- **Max rounds**: 1

### 6. `file_read`
- **When to use**: After `file_lookup` to get the ID of a file the user is referencing
- **Arguments**: `{"file_id": {"type": "integer", "description": "The frame ID of the file to read"}}`
- **Handler**: `_handle_file_read` — returns stored content from `file_content_preview` slot + actual file from `/app/data/`
- **Returns**: Combined stored + actual content (first 10000 chars)
- **Max rounds**: 1

### 7. `file_write`
- **When to use**: User says "create a new file called X" or "write Y to file Z"
- **Arguments**: 
  - `{"name": {"type": "string", "description": "Name of the file (without extension)"}}`
  - `{"content": {"type": "string", "description": "The full content to write to the file"}}`
  - `{"file_type": {"type": "string", "description": "File extension without dot (e.g. 'ics', 'csv', 'txt')"}}`
- **Handler**: `_handle_file_write` — saves to `/app/data/`, creates frame with metadata slots
- **Features**: Timestamp-based safe filename, reliability score 0.7/0.8, slot storage (`file_name`, `file_ext`, `file_size`, `file_content_preview`)
- **Max rounds**: 1

### 8. `file_update`
- **When to use**: After `file_lookup` to update existing file content
- **Arguments**: 
  - `{"file_id": {"type": "integer", "description": "The frame ID of the file to update"}}`
  - `{"new_content": {"type": "string", "description": "The new content to write to the file"}}`
- **Handler**: `_handle_file_update` — overwrites file in `/app/data/`, updates memory slots
- **Max rounds**: 1

### 9. `file_delete`
- **When to use**: User wants to remove a file they uploaded or created
- **Arguments**: `{"file_id": {"type": "integer", "description": "The frame ID of the file to delete"}}`
- **Handler**: `_handle_file_delete` — deletes from `/app/data/` and removes memory frame
- **Max rounds**: 1

### 10. `file_search`
- **When to use**: User wants to find specific information inside their files
- **Arguments**: 
  - `{"query": {"type": "string", "description": "The text term to search for inside file contents"}}`
  - `{"file_type": {"type": "string", "description": "Optional file extension filter (e.g. 'ics', 'csv')"}}`
- **Handler**: `_handle_file_search` — searches all `file_` frames, simple text search in `file_content_preview`
- **Returns**: JSON array of matches with `file_name`, `file_ext`, `file_size`, `match_snippet`
- **Max rounds**: 1

---

## Tool Loop

`run_tool_loop()` in `tools.py:538` manages the multi-round conversation:

```python
for _ in range(max_rounds=3):
    # 1. Send messages + tool defs to model
    response = await llm_client.chat(convo, tools=defs)
    
    # 2. If no tool calls → done, return prose response
    if not response.tool_calls:
        return response
    
    # 3. Append model's tool calls to conversation
    convo.append(role="assistant" message with tool_calls)
    
    # 4. Execute each tool locally
    for tc in tool_calls:
        result = await tool.handler(**tc.arguments)
    
    # 5. Append tool results as role="tool" messages
    convo.append(role="tool" content=str(result), name=tc.name)
# After 3 rounds, model answers without tools
```

**Key design decisions**:
- Bounded by `MAX_TOOL_ROUNDS=3` to prevent infinite loops
- Thinking chains (`thinking` field) are preserved but never fed back to the model (§6.3)
- Tool results are stored as `role="tool"` messages in the conversation context
- Each tool execution is wrapped in try/except with error reporting to the model

---

## Principles Alignment

| Principle | How Tools Implement It |
|-----------|----------------------|
| **Model-first correction** | Model decides when to call tools; no scripted logic for file operations |
| **No templated responses** | Model generates file content (iCal, CSV, JSON) via `file_write` |
| **Lean on model flexibility** | Agent reasons about which tool to use, not hardcoded branches |
| **Scheduled tasks are memory** | Files stored as frames/slots with embeddings, queryable like any memory |
| **Clean ship** | All tools in one module (`tools.py`), shared via `builtin_tools()`, lint clean |
| **Stability: no regressions** | Tool loop bounded; each tool has specific, tested behavior |

---

## Adding New Tools

To add a new tool:

1. **Add handler function** in `tools.py` (async, returns `str`)
2. **Add `AssistantTool` entry** in `builtin_tools()` list:
   ```python
   AssistantTool(
       name="your-tool-name",
       description="When to use this tool",
       parameters={"type": "object", "properties": {...}, "required": [...]}",
       handler=_handle_your_tool,
   ),
   ```
3. **Ensure handler is importable** from orchestrator if needed
4. **Run ruff** to verify no lint issues

---

## Missing / Future Tools

The following are documented as needed but not yet implemented:

| Tool | Purpose | Status |
|------|---------|--------|
| `calendar_create` / `calendar_update` | High-level iCal event creation | ❌ Would wrap `file_write` + iCal format |
| `csv_generate` | Generate CSV from data | ❌ Could wrap `file_write` with CSV formatting |
| `json_pretty` | Format/transform JSON | ❌ Could wrap `file_write` |
| `document_search` | Semantic search across files | ❌ Would need embeddings + vector search |

These would be added following the same pattern as existing tools.

---

## Tool Execution Flow (Summary)

```
User prompt
    ↓
Router classifies (functional/introspective/scheduled)
    ↓
Orchestrator.chat():
  1. Retrieve memory context
  2. Decide: search? extract facts? 
  3. If tools needed: run_tool_loop(llm, messages, tools)
     - Model returns tool_calls
     - Each tool executes locally
     - Results fed back as role="tool" messages
     - Repeats up to 3 rounds
     - Model answers in prose
  4. LLM call with tool results in context
  5. Store episode, update frames/slots
  6. Return ChatResponse
```

All file operations go through this same loop, ensuring the model always decides when and how to operate on files.