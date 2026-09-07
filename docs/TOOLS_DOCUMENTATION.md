# Tools Documentation

## Overview
The assistant uses native Ollama tool-calling. The chat model decides when to call local tools, and results are fed back into the conversation context. All tools are local-first — no external/cloud APIs.

## Tool Registry
Tools are registered in `builtin_tools()` via `AssistantTool` dataclasses (`name`, `description`, `parameters`, `handler`). Definitions are sent to Ollama's `/api/chat` with `tools`; the model returns `tool_calls`, executed locally via `run_tool_loop()`.

## Registered Tools
### 1. `get_current_datetime`
- **When**: Any question about "today", "tomorrow", date offsets.
- **Arguments**: `{}`
- **Handler**: `_handle_datetime` — returns `YYYY-MM-DD HH:MM AM/PM (TZ)`.
- **Max rounds**: 1.

### 2. `calculate`
- **When**: Math beyond trivial mental math.
- **Arguments**: `{"expression": "string"}` — arithmetic, allowed chars `0123456789+-*/%(). `.
- **Handler**: `_handle_calculate` — evaluates via Python `eval()` with restricted builtins.
- **Max rounds**: 1.

### 3. `fetch_url`
- **When**: User provides a specific URL; search can't reach it.
- **Arguments**: `{"url": "string"}` — must be http/https.
- **Handler**: `_make_fetch_url_handler` — strips HTML, 500KB content limit, auto-extracts facts to memory.
- **Max rounds**: 1.

### 4. `web_search`
- **When**: Answer needs facts not already in memory; search is required.
- **Arguments**: `{"query": "string", "num_results": "integer"}` (default 5).
- **Handler**: `_make_search_handler` — SearXNG primary, Brave optional (`BRAVE_ENABLED=true` + `BRAVE_API_KEY`).
- **Features**: Embedding-based relevance filtering (`filter_relevant`), source reliability.
- **Only enabled when**: `BRAVE_ENABLED=true` + `BRAVE_API_KEY`, OR SearXNG running locally.
- **Max rounds**: 1.

### 5. `file_lookup`
- **When**: User refers to "the file called X" or "the ical file".
- **Arguments**: `{"name_pattern": "string"}` (optional `{"file_type": "string"}`).
- **Handler**: `_handle_file_lookup` — searches frames with `file_` prefix, matches `file_name` slot.
- **Returns**: JSON array of matches with `file_name`, `file_ext`, `file_size`, `content_preview`.
- **Max rounds**: 1.

### 6. `file_read`
- **When**: After `file_lookup` to get the file ID.
- **Arguments**: `{"file_id": "integer"}`.
- **Handler**: `_handle_file_read` — returns stored `file_content_preview` + actual file from `/app/data/`.
- **Returns**: Combined content (first 10000 chars).
- **Max rounds**: 1.

### 7. `file_write`
- **When**: User says "create a new file called X" or "write Y to file Z".
- **Arguments**: `{"name": "string", "content": "string", "file_type": "string"}` (extension without dot).
- **Handler**: `_handle_file_write` — saves to `/app/data/`, creates frame with metadata slots.
- **Features**: Timestamp-based safe filename, reliability score 0.7/0.8, slots (`file_name`, `file_ext`, `file_size`, `file_content_preview`).
- **Max rounds**: 1.

### 8. `file_update`
- **When**: After `file_lookup` to update existing file content.
- **Arguments**: `{"file_id": "integer", "new_content": "string"}`.
- **Handler**: `_handle_file_update` — overwrites file in `/app/data/`, updates memory slots.
- **Max rounds**: 1.

### 9. `file_delete`
- **When**: User wants to remove an uploaded/created file.
- **Arguments**: `{"file_id": "integer"}`.
- **Handler**: `_handle_file_delete` — deletes from `/app/data/` and removes memory frame.
- **Max rounds**: 1.

### 10. `file_search`
- **When**: User wants to find specific information inside their files.
- **Arguments**: `{"query": "string"}` (optional `{"file_type": "string"}`).
- **Handler**: `_handle_file_search` — searches all `file_` frames, simple text search in `file_content_preview`.
- **Returns**: JSON array of matches with `file_name`, `file_ext`, `file_size`, `match_snippet`.
- **Max rounds**: 1.

## Tool Loop
`run_tool_loop()` (`tools.py:538`) manages the multi-round conversation:
1. Send messages + tool defs to model.
2. If no tool calls → done, return prose response.
3. Append model's tool calls to conversation.
4. Execute each tool locally.
5. Append tool results as `role="tool"` messages.
6. After 3 rounds (`MAX_TOOL_ROUNDS=3`), model answers without tools.

**Key design decisions**:
- Bounded by `MAX_TOOL_ROUNDS=3` to prevent infinite loops.
- Thinking chains (`thinking` field) are preserved but never fed back to the model.
- Tool results stored as `role="tool"` messages in conversation context.
- Each tool execution wrapped in try/except with error reporting to the model.

## Principles Alignment
| Principle | How Tools Implement It |
|---|---|
| Model-first correction | Model decides when to call tools; no scripted logic. |
| No templated responses | Model generates file content (iCal, CSV, JSON) via `file_write`. |
| Lean on model flexibility | Agent reasons about which tool to use; not hardcoded branches. |
| Clean ship | All tools in one module (`tools.py`), shared via `builtin_tools()`, lint clean. |
| Stability: no regressions | Tool loop bounded; each tool has specific, tested behavior. |

## Adding New Tools
1. Add handler function in `tools.py` (async, returns `str`).
2. Add `AssistantTool` entry in `builtin_tools()`:
   ```python
   AssistantTool(
       name="your-tool-name",
       description="When to use this tool",
       parameters={"type": "object", "properties": {...}, "required": [...]}",
       handler=_handle_your_tool,
   ),
   ```
3. Ensure handler is importable from orchestrator if needed.
4. Run ruff to verify no lint issues.

## Future Tools (planned)
- `calendar_create` / `calendar_update` — high-level iCal event creation.
- `csv_generate` — generate CSV from data.
- `json_pretty` — format/transform JSON.
- `document_search` — semantic search across files.

These follow the same pattern as existing tools.
