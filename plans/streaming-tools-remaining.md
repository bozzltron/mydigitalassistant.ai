# Streaming Tools - Remaining Work Plan

## Status: Infrastructure Complete ✅
The streaming infrastructure is now operational:
- Chat streaming (`/chat/stream`) returns SSE events correctly
- Non-streaming chat (`/chat`) works with tools enabled
- Tool calling loop executes and returns results

## Remaining Issues (Priority Order)

### 1. Fix `list_files` Tool Bug (Critical)
**Error**: `invalid literal for int() with base 10: ''`
**Location**: `assistant/backend/pipeline/tool_executor.py` line 847
**Root Cause**: `slots_dict.get("file_size")` returns empty string, then cast to int fails
**Fix**: Add default value handling for missing `file_size` slot

```python
# Current (line 847):
"file_size": slots_dict.get("file_size"),

# Fixed:
"file_size": int(slots_dict.get("file_size", "0")) if slots_dict.get("file_size") else None,
```

### 2. Update `.env.example` with Working Defaults
**Files to update**: `.env.example` 
**Changes needed**:
- `UTILITY_MODEL=qwen3.5:4b` (not qwen2.5:3b)
- Add `TOOLS_MODEL=qwen3.5:4b`, `TOOLS_NUM_CTX=4096`, `TOOLS_KEEP_ALIVE=-1`
- Update comments to reflect available models

### 3. Enable Episode Search (Optional - Performance)
**Current**: `retrieval_episode_limit = 0` (disabled)
**Goal**: Re-enable with proper sqlite-vec index optimization
**Blocked by**: Need to investigate sqlite-vec index creation for episode_embeddings table

### 4. Tool Streaming (Phase 4)
**Current**: Only final answer streams; tool calls execute synchronously
**Goal**: Stream tool calls and results in real-time via SSE
**File**: `assistant/backend/pipeline/streaming.py` - `stream_tool_loop()`

### 5. Add Missing Model Documentation
**Update**: Document the actual working model fleet in AGENTS.md
- CHAT: qwen2.5:7b
- UTILITY: qwen3.5:4b
- TOOLS: qwen3.5:4b (using same as utility for now)
- EMBEDDING: nomic-embed-text
- MATH: qwen3-coder:30b

---

## Testing Checklist

- [ ] `list_files` tool works without error
- [ ] `read_file`, `write_file`, `edit_file`, `glob` tools work
- [ ] Memory tools (`upsert_slot`, `recall`, `search_episodes`) work
- [ ] Search tools (`web_search`, `fetch_url`) work
- [ ] Streaming response includes tool call events
- [ ] All critical tests pass (`test_daily_schedule.py`, `test_review_fixes.py`)
- [ ] Lint passes (`ruff check .`)

---

## Notes for Future

The `qwen3.5:4b` model serves dual role as both utility and tools model. For optimal performance, a dedicated 1.5B function-calling model (qwen2.5-coder:1.5b or smollm2:1.7b) should be pulled when available, then update `TOOLS_MODEL` in `.env`.