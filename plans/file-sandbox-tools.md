# File Sandbox Tools Plan

## Overview
Enable the agent to create, read, edit, delete, and discover files in its own directory sandbox (`/app/data`). This extends the existing file upload/view infrastructure with full CRUD tooling exposed via Ollama's native tool calling API, maintaining privacy-first local-only operation.

## Current State Analysis

### Existing Infrastructure (Working)
- **File upload API**: `POST /files/upload` → stores in `/app/data`, creates memory frames with slots
- **File listing API**: `GET /files/list` → returns frames with `source_type="file_upload"`
- **File read API**: `GET /files/{frame_id}/content` → reads from disk via `file_safe_name` slot
- **File delete API**: `DELETE /files/{frame_id}` → soft-deletes frame, removes physical file
- **Memory integration**: CSV creates parent frame + row frames with `part_of` associations
- **Tool registry**: `builtin_tools()` + `execute_tool()` pattern with Pydantic validation
- **Tool loop**: `run_tool_loop()` orchestrates multi-turn tool use until `finalize()`

### Broken/Missing Tools
| Tool | Status | Issue |
|------|--------|-------|
| `list_files` | ✅ Works | Returns internal `frame_name` not user-visible names |
| `read_file` | ⚠️ Broken | Requires `frame_id`/`frame_name` (internal IDs model can't discover) |
| `recall` | ❌ Broken | Returns `[]` — embedding not implemented |
| `write_file` | ❌ Missing | No tool to create new files |
| `edit_file` | ❌ Missing | No tool to modify files |
| `delete_file` | ❌ Missing | Only API endpoint exists |
| `glob` | ❌ Missing | No file discovery by pattern |

### Root Causes
1. **Tool args use internal IDs** — `read_file` needs `frame_name` like `"file_upload_20260922_213538_users.csv"` which the model never sees in memory context
2. **`recall` tool stubbed** — Comment admits "For now, return empty results as embedding requires LLM client"
3. **No sandbox enforcement layer** — Path safety assumed but not centralized
4. **System prompt guidance vague** — "Use your judgment" without concrete chaining examples

---

## Requirements

### Functional Requirements

#### FR1: File Creation (`write_file`)
- Create new files in sandbox with arbitrary content
- Support overwrite protection (opt-in)
- Auto-create parent directories
- Integrate with memory: create frame with slots (`file_name`, `file_ext`, `file_size`, `file_safe_name`, `file_content_preview`)
- Return relative path and metadata

#### FR2: File Reading (`read_file`)
- Read file content by **user-visible filename** (e.g., `"notes/todo.txt"`)
- Resolve to internal frame via `file_name` slot lookup
- Return full content + metadata
- Size limit: 1MB per read (configurable)

#### FR3: File Editing (`edit_file`)
- Surgical find-and-replace: exact `old_text` → `new_text`
- Support multiple occurrences (replace all)
- Return change count
- Atomic: write to temp file then rename

#### FR4: File Deletion (`delete_file`)
- Delete by user-visible filename
- Remove physical file from `/app/data`
- Soft-delete memory frame (set `priority=0`)
- Clean up row frames for CSV parents

#### FR5: File Discovery (`glob` + `list_files`)
- `glob(pattern)`: Find files matching pattern (e.g., `"notes/*.md"`, `"**/*.py"`)
- `list_files()`: Return all files with user-visible names, sizes, types
- Both return relative paths from sandbox root

#### FR6: Semantic Recall (`recall`)
- Embed query → search frames via sqlite-vec + graph walk
- Return frames/slots/associations matching query
- Filter by `frame_types`, `min_confidence`
- This is the primary "search my memory" tool

### Non-Functional Requirements

| Requirement | Specification |
|-------------|---------------|
| **Sandbox enforcement** | All paths resolved relative to `/app/data`; traversal blocked; symlinks validated |
| **Size limits** | Write: 10MB; Read: 1MB; Glob: 1000 results max |
| **Atomicity** | Write/edit use temp-file + rename; partial writes never visible |
| **Memory sync** | Tool-created files auto-create frames; edits update preview slots |
| **Model guidance** | System prompt includes concrete tool-chaining examples |
| **Observability** | Structured logs for each tool call: tool, args, latency, result |
| **Test coverage** | Regression tests for each tool + integration flow |

---

## Design Principle Alignment

| Principle | Application |
|-----------|-------------|
| **Model-first correction** | Tools exposed via Ollama tools API; model decides when to use; no hardcoded routing |
| **No templated responses** | Model generates responses from tool results; no "File created successfully" templates |
| **Lean on model flexibility** | Model chains `list_files` → `read_file` → `edit_file` based on task |
| **Scheduled tasks are memory** | Files created by scheduled tasks become frames like any other |
| **Clean ship** | Single `filesystem.py` for sandbox logic; no dead code; unused imports caught by ruff |
| **Stability: no regressions** | Each tool gets unit test + integration test before merge |
| **Safety & Privacy** | All operations local; no network calls; sandbox prevents escape; user consent not needed for local files |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Tools Layer                        │
│  write_file  │  read_file  │  edit_file  │  delete_file  │ glob │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Sandbox Enforcement Layer                     │
│  resolve_sandbox_path()  │  validate_path_safety()              │
│  - Blocks ../            │  - Validates symlinks                │
│  - Enforces size limits  │  - Normalizes paths                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Memory Integration Layer                     │
│  create_file_frame()  │  update_file_frame()  │  delete_file_frame() │
│  - Auto-create on write      - Update preview on edit         │
│  - Row frames for CSV        - Cascade delete for CSV parents │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Physical Storage                          │
│                        /app/data (Docker volume)                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: Sandbox Foundation (Day 1)

#### 1.1 Create `assistant/backend/pipeline/filesystem.py`
```python
"""Sandbox filesystem operations with safety enforcement."""

from pathlib import Path
from assistant.backend.config import settings

SANDBOX_ROOT = Path("/app/data").resolve()
MAX_FILE_SIZE = 10_000_000  # 10MB
MAX_READ_SIZE = 1_000_000   # 1MB
MAX_GLOB_RESULTS = 1000

def resolve_sandbox_path(relative_path: str) -> Path:
    """Resolve relative path within sandbox, preventing traversal."""
    requested = (SANDBOX_ROOT / relative_path).resolve()
    try:
        requested.relative_to(SANDBOX_ROOT)
    except ValueError:
        raise ValueError(f"Path '{relative_path}' escapes sandbox")
    return requested

def validate_path_safety(path: Path) -> None:
    """Additional safety checks."""
    if path.is_symlink():
        target = path.resolve()
        try:
            target.relative_to(SANDBOX_ROOT)
        except ValueError:
            raise ValueError("Symlink points outside sandbox")
    if path.exists() and path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds {MAX_FILE_SIZE} byte limit")

def list_sandbox_files(pattern: str = "**/*") -> list[dict]:
    """List files matching glob pattern."""
    matches = list(SANDBOX_ROOT.glob(pattern))
    files = []
    for m in matches[:MAX_GLOB_RESULTS]:
        if m.is_file():
            try:
                rel = m.relative_to(SANDBOX_ROOT)
                files.append({
                    "path": str(rel),
                    "size": m.stat().st_size,
                    "modified": m.stat().st_mtime,
                    "ext": m.suffix.lstrip(".").lower()
                })
            except ValueError:
                continue
    return files
```

#### 1.2 Add Tool Arg Schemas to `tools.py`
```python
class WriteFileArgs(BaseModel):
    path: str = Field(..., description="Relative path in sandbox (e.g., 'notes/todo.txt')")
    content: str = Field(..., description="File content to write")
    overwrite: bool = Field(False, description="Allow overwriting existing file")

class ReadFileArgs(BaseModel):
    path: str = Field(..., description="Relative path in sandbox (e.g., 'notes/todo.txt')")

class EditFileArgs(BaseModel):
    path: str = Field(..., description="Relative path in sandbox")
    old_text: str = Field(..., description="Exact text to replace")
    new_text: str = Field(..., description="Replacement text")
    replace_all: bool = Field(True, description="Replace all occurrences")

class DeleteFileArgs(BaseModel):
    path: str = Field(..., description="Relative path in sandbox")

class GlobArgs(BaseModel):
    pattern: str = Field(..., description="Glob pattern (e.g., '*.txt', 'notes/**/*.md')")
```

#### 1.3 Fix `RecallArgs` (Already Exists)
```python
# Already in tools.py - just needs working executor
class RecallArgs(BaseModel):
    query: str = Field(..., description="Natural language query")
    frame_types: list[str] | None = Field(None, description="Frame name patterns")
    max_results: int = Field(10, ge=1)
    min_confidence: float = Field(0.3, ge=0, le=1)
    include_associations: bool = Field(True)
```

### Phase 2: Tool Executors (Day 1-2)

#### 2.1 Add to `tool_executor.py`

**Dependencies needed:** Pass `embed_fn` to executors that need it.

```python
# Global embed function (set during initialization)
_embed_fn: Callable | None = None

def init_store(db_path: str, embed_fn: Callable | None = None) -> None:
    global _store, _embed_fn
    _store = MemoryStore(db_path)
    _embed_fn = embed_fn
    _register_builtin_tools()
```

**Executor: `write_file`**
```python
async def execute_write_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    try:
        rel_path = args["path"]
        content = args["content"]
        overwrite = args.get("overwrite", False)
        
        path = resolve_sandbox_path(rel_path)
        validate_path_safety(path)
        
        if path.exists() and not overwrite:
            return ToolResult(success=False, error=f"File exists: {rel_path} (use overwrite=true)")
        
        # Atomic write
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.rename(path)
        
        # Create/update memory frame
        frame_name = f"file_{path.name}"
        frame = await _store.get_frame_by_name(frame_name)
        if not frame:
            frame = await _store.create_frame(
                frame_name, "entity",
                source_type="file_create", owner_user_id=int(user_id), source_reliability=0.8
            )
        
        # Update slots
        await _store.upsert_slot(frame.id, "file_name", path.name, source_type="file_create")
        await _store.upsert_slot(frame.id, "file_ext", path.suffix.lstrip("."), source_type="file_create")
        await _store.upsert_slot(frame.id, "file_size", str(len(content)), source_type="file_create")
        await _store.upsert_slot(frame.id, "file_safe_name", path.name, source_type="file_create")
        await _store.upsert_slot(frame.id, "file_content_preview", content[:200], source_type="file_create")
        
        return ToolResult(success=True, data={
            "path": rel_path, "size": len(content), "frame_id": frame.id
        })
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

**Executor: `read_file`**
```python
async def execute_read_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    try:
        rel_path = args["path"]
        path = resolve_sandbox_path(rel_path)
        validate_path_safety(path)
        
        if not path.exists():
            return ToolResult(success=False, error=f"File not found: {rel_path}")
        
        if path.stat().st_size > MAX_READ_SIZE:
            return ToolResult(success=False, error=f"File too large (>1MB): {rel_path}")
        
        content = path.read_text(encoding="utf-8", errors="replace")
        
        # Also update memory frame preview if it exists
        frame = await _store.get_frame_by_name(f"file_{path.name}")
        if frame:
            await _store.upsert_slot(frame.id, "file_content_preview", content[:200], source_type="file_read")
        
        return ToolResult(success=True, data={
            "path": rel_path, "content": content, "size": len(content)
        })
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

**Executor: `edit_file`**
```python
async def execute_edit_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    try:
        rel_path = args["path"]
        old_text = args["old_text"]
        new_text = args["new_text"]
        replace_all = args.get("replace_all", True)
        
        path = resolve_sandbox_path(rel_path)
        validate_path_safety(path)
        
        if not path.exists():
            return ToolResult(success=False, error=f"File not found: {rel_path}")
        
        content = path.read_text(encoding="utf-8", errors="replace")
        if old_text not in content:
            return ToolResult(success=False, error="old_text not found in file")
        
        if replace_all:
            new_content = content.replace(old_text, new_text)
            changes = content.count(old_text)
        else:
            new_content = content.replace(old_text, new_text, 1)
            changes = 1
        
        if new_content == content:
            return ToolResult(success=False, error="No changes made")
        
        # Atomic write
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(new_content, encoding="utf-8")
        tmp_path.rename(path)
        
        # Update memory frame
        frame = await _store.get_frame_by_name(f"file_{path.name}")
        if frame:
            await _store.upsert_slot(frame.id, "file_content_preview", new_content[:200], source_type="file_edit")
            await _store.upsert_slot(frame.id, "file_size", str(len(new_content)), source_type="file_edit")
        
        return ToolResult(success=True, data={
            "path": rel_path, "changes": changes, "new_size": len(new_content)
        })
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

**Executor: `delete_file`**
```python
async def execute_delete_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    try:
        rel_path = args["path"]
        path = resolve_sandbox_path(rel_path)
        
        if not path.exists():
            return ToolResult(success=False, error=f"File not found: {rel_path}")
        
        # Find and soft-delete memory frame
        frame = await _store.get_frame_by_name(f"file_{path.name}")
        if frame:
            # Check if CSV parent with row frames
            row_count_slot = await _store.get_slot(frame.id, "row_count")
            if row_count_slot and int(row_count_slot.value or "0") > 0:
                # Soft-delete row frames too
                associations = await _store.get_all_associations_for_frame(frame.id)
                for assoc in associations:
                    if assoc.relation_type == "part_of":
                        await _store.forget_frame(assoc.target_frame)
            await _store.forget_frame(frame.id)
        
        # Remove physical file
        path.unlink()
        
        return ToolResult(success=True, data={"path": rel_path})
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

**Executor: `glob`**
```python
async def execute_glob(args: dict, user_id: str, session_id: str) -> ToolResult:
    try:
        pattern = args["pattern"]
        files = list_sandbox_files(pattern)
        return ToolResult(success=True, data={"files": files, "count": len(files)})
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

**Executor: `recall` (FIX THE BROKEN ONE)**
```python
async def execute_recall(args: dict, user_id: str, session_id: str) -> ToolResult:
    if _store is None or _embed_fn is None:
        return ToolResult(success=False, error="MemoryStore or embed_fn not initialized")
    
    try:
        query = args.get("query", "")
        if not query:
            return ToolResult(success=False, error="query required")
        
        # Embed query
        embedding_resp = await _embed_fn(query)
        embedding = embedding_resp.embedding if hasattr(embedding_resp, 'embedding') else embedding_resp
        
        # Search frames
        frame_types = args.get("frame_types")
        max_results = args.get("max_results", 10)
        min_confidence = args.get("min_confidence", 0.3)
        include_associations = args.get("include_associations", True)
        
        results = await _store.search_similar_frames(
            embedding=embedding,
            user_id=int(user_id),
            limit=max_results,
            min_distance=1.0 - min_confidence,
        )
        
        # Format results
        formatted = []
        for frame, slots, similarity in results:
            if frame_types and not any(frame.name.startswith(ft) for ft in frame_types):
                continue
            
            slot_data = {s.key: {"value": s.value, "confidence": s.confidence} for s in slots}
            
            assoc_data = []
            if include_associations:
                for assoc in frame.associations:
                    assoc_data.append({
                        "source": assoc.source_frame,
                        "target": assoc.target_frame,
                        "relation": assoc.relation_type,
                        "confidence": assoc.confidence
                    })
            
            formatted.append({
                "frame_id": frame.id,
                "frame_name": frame.name,
                "frame_type": frame.type,
                "confidence": frame.confidence,
                "similarity": similarity,
                "slots": slot_data,
                "associations": assoc_data
            })
        
        return ToolResult(success=True, data={"results": formatted, "count": len(formatted)})
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

#### 2.2 Register Tools in `_register_builtin_tools()`
```python
register_tool("write_file", WriteFileArgs, execute_write_file, timeout=10.0)
register_tool("read_file", ReadFileArgs, execute_read_file, timeout=10.0)
register_tool("edit_file", EditFileArgs, execute_edit_file, timeout=10.0)
register_tool("delete_file", DeleteFileArgs, execute_delete_file, timeout=10.0)
register_tool("glob", GlobArgs, execute_glob, timeout=10.0)
# recall already registered - just needs working executor
```

### Phase 3: Tool Definitions & Model Guidance (Day 2)

#### 3.1 Add to `builtin_tools()` in `tools.py`
```python
tools = [
    # ... existing tools ...
    _make_def(
        "write_file",
        "Create a new file or overwrite an existing file in the assistant's sandbox directory. "
        "Use for: saving notes, creating scripts, writing reports, generating code, drafting documents. "
        "Path is relative to sandbox root (e.g., 'notes/meeting.txt', 'scripts/analyze.py'). "
        "Set overwrite=true to replace existing file.",
        WriteFileArgs,
    ),
    _make_def(
        "read_file",
        "Read the full content of a file in the sandbox by its relative path. "
        "Use when you need to examine a file's contents before editing or referencing it. "
        "Path is relative to sandbox root (e.g., 'notes/meeting.txt').",
        ReadFileArgs,
    ),
    _make_def(
        "edit_file",
        "Make a surgical edit to an existing file by replacing exact text. "
        "Use for: modifying config files, fixing code, updating documents, correcting typos. "
        "Provide the exact old_text to replace and the new_text. "
        "Set replace_all=false to replace only the first occurrence.",
        EditFileArgs,
    ),
    _make_def(
        "delete_file",
        "Delete a file from the sandbox. Also removes the associated memory frame. "
        "Use when a file is no longer needed. Path is relative to sandbox root.",
        DeleteFileArgs,
    ),
    _make_def(
        "glob",
        "Find files matching a glob pattern in the sandbox. "
        "Use for: discovering files, listing directory contents, finding files by extension. "
        "Patterns: '*.txt' (all txt files), 'notes/*.md' (md files in notes/), "
        "'**/*.py' (all Python files recursively).",
        GlobArgs,
    ),
    _make_def(
        "recall",
        "Semantic memory lookup: search your memory (frames, slots, associations) by natural language query. "
        "Returns matching frames with slots and similarity scores. "
        "Use when the user asks about something you might know, or you need to find related information. "
        "Filter by frame_types (e.g., ['person_*', 'event_*']) and min_confidence.",
        RecallArgs,
    ),
]
```

#### 3.2 Update System Prompt in `llm_client.py` (lines 523-544)
Replace vague guidance with concrete examples:

```python
# In build_system_prompt(), replace the file tools section:
"""
- You have access to your sandbox filesystem via these tools:
  • list_files() — List all files with names, sizes, types
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
"""
```

#### 3.3 Fix `read_file` Tool Description
Update the existing `read_file` tool to use `path` (user-visible) instead of `frame_id`/`frame_name`.

### Phase 4: Memory Integration (Day 2-3)

#### 4.1 CSV Row Frame Support for `write_file`
When writing a `.csv` file, parse and create row frames like upload does:

```python
# In execute_write_file, after creating parent frame:
if path.suffix.lower() == ".csv":
    import csv
    from io import StringIO
    reader = csv.reader(StringIO(content))
    rows = list(reader)
    if rows:
        headers = rows[0]
        for i, row in enumerate(rows[1:], 1):
            row_frame = await _store.create_frame(
                f"file_{path.name}_row_{i}", "record",
                source_type="csv_row", owner_user_id=int(user_id)
            )
            for col, val in zip(headers, row):
                slot_key = re.sub(r'[^a-zA-Z0-9_]', '_', col.lower().strip())
                await _store.upsert_slot(row_frame.id, slot_key, val, source_type="csv_row")
            await _store.create_association(frame.id, row_frame.id, "part_of")
        await _store.upsert_slot(frame.id, "row_count", str(len(rows)-1), source_type="file_create")
        await _store.upsert_slot(frame.id, "columns", json.dumps(headers), source_type="file_create")
```

#### 4.2 Update `list_files` Tool to Return User-Friendly Data
Modify `execute_list_files` to include the resolved relative path:

```python
# In execute_list_files, add to each file dict:
"path": str(Path(safe_name).relative_to(SANDBOX_ROOT)) if safe_name else None
```

### Phase 5: Tests (Day 3)

#### 5.1 Unit Tests in `test_files.py`
```python
class TestFileSandboxTools:
    """Regression tests for agent file sandbox tools."""
    
    @pytest.mark.asyncio
    async def test_write_file_creates_file_and_frame(self, store, stub_llm):
        from assistant.backend.pipeline.tool_executor import execute_write_file
        
        result = await execute_write_file(
            {"path": "test_agent.txt", "content": "Hello from agent"},
            user_id="1", session_id="test"
        )
        assert result.success
        assert result.data["path"] == "test_agent.txt"
        
        # Verify physical file
        from pathlib import Path
        assert Path("/app/data/test_agent.txt").read_text() == "Hello from agent"
        
        # Verify memory frame
        frame = await store.get_frame_by_name("file_test_agent.txt")
        assert frame is not None
        slots = await store.get_slots_for_frame(frame.id)
        assert any(s.key == "file_content_preview" and "Hello" in s.value for s in slots)
    
    @pytest.mark.asyncio
    async def test_write_file_rejects_traversal(self, store, stub_llm):
        result = await execute_write_file(
            {"path": "../../../etc/passwd", "content": "evil"},
            user_id="1", session_id="test"
        )
        assert not result.success
        assert "escapes sandbox" in result.error
    
    @pytest.mark.asyncio
    async def test_read_file_by_user_visible_name(self, store, stub_llm):
        # Setup via write_file
        await execute_write_file({"path": "notes/readme.md", "content": "# Readme\nContent here"}, "1", "test")
        
        result = await execute_read_file({"path": "notes/readme.md"}, "1", "test")
        assert result.success
        assert result.data["content"] == "# Readme\nContent here"
    
    @pytest.mark.asyncio
    async def test_edit_file_surgical_replace(self, store, stub_llm):
        await execute_write_file({"path": "edit_test.txt", "content": "foo bar baz"}, "1", "test")
        
        result = await execute_edit_file(
            {"path": "edit_test.txt", "old_text": "bar", "new_text": "BAR"},
            "1", "test"
        )
        assert result.success
        assert result.data["changes"] == 1
        
        read_result = await execute_read_file({"path": "edit_test.txt"}, "1", "test")
        assert "foo BAR baz" in read_result.data["content"]
    
    @pytest.mark.asyncio
    async def test_delete_file_removes_file_and_frame(self, store, stub_llm):
        await execute_write_file({"path": "to_delete.txt", "content": "delete me"}, "1", "test")
        
        result = await execute_delete_file({"path": "to_delete.txt"}, "1", "test")
        assert result.success
        
        # Physical file gone
        assert not Path("/app/data/to_delete.txt").exists()
        
        # Frame soft-deleted
        frame = await store.get_frame_by_name("file_to_delete.txt")
        assert frame is not None
        assert frame.priority == 0
    
    @pytest.mark.asyncio
    async def test_glob_finds_files_by_pattern(self, store, stub_llm):
        await execute_write_file({"path": "notes/a.txt", "content": "a"}, "1", "test")
        await execute_write_file({"path": "notes/b.txt", "content": "b"}, "1", "test")
        await execute_write_file({"path": "scripts/c.py", "content": "c"}, "1", "test")
        
        result = await execute_glob({"pattern": "notes/*.txt"}, "1", "test")
        assert result.success
        assert result.data["count"] == 2
        paths = [f["path"] for f in result.data["files"]]
        assert "notes/a.txt" in paths
        assert "scripts/c.py" not in paths
    
    @pytest.mark.asyncio
    async def test_recall_returns_semantic_matches(self, store, stub_llm, stub_embed):
        # Create some frames with known content
        frame = await store.create_frame("person_alice", "person", owner_user_id=1)
        await store.upsert_slot(frame.id, "name", "Alice", source_type="test")
        await store.upsert_slot(frame.id, "role", "engineer", source_type="test")
        
        # Embed and index
        embedding = [0.1] * 768  # stub
        await store.store_frame_embedding(frame.id, embedding, "nomic-embed-text")
        
        result = await execute_recall({"query": "Alice engineer"}, "1", "test")
        assert result.success
        assert result.data["count"] >= 1
        assert any("Alice" in str(r["slots"]) for r in result.data["results"])
    
    @pytest.mark.asyncio
    async def test_csv_write_creates_row_frames(self, store, stub_llm):
        csv_content = "name,email\nAlice,a@b.com\nBob,b@c.com"
        result = await execute_write_file(
            {"path": "users.csv", "content": csv_content},
            "1", "test"
        )
        assert result.success
        
        # Verify parent frame has row_count
        frame = await store.get_frame_by_name("file_users.csv")
        row_count = await store.get_slot(frame.id, "row_count")
        assert row_count.value == "2"
        
        # Verify row frames exist
        associations = await store.get_all_associations_for_frame(frame.id)
        part_of = [a for a in associations if a.relation_type == "part_of"]
        assert len(part_of) == 2
```

#### 5.2 Integration Test: Full Tool Chain
```python
@pytest.mark.asyncio
async def test_full_file_workflow_via_tool_loop(orchestrator, store, stub_llm):
    """End-to-end: model uses tools to create → read → edit → delete."""
    # This requires a real or stubbed LLM that can call tools
    # Use the existing run_tool_loop with a test model
    pass  # Implement with test double that simulates tool calls
```

### Phase 6: Configuration & Polish (Day 3-4)

#### 6.1 Add Settings to `config.py`
```python
# In Settings class
tools_enabled: bool = Field(default=True, description="Enable tool calling")
sandbox_max_file_size: int = Field(default=10_000_000, description="Max file size in bytes")
sandbox_max_read_size: int = Field(default=1_000_000, description="Max read size in bytes")
sandbox_max_glob_results: int = Field(default=1000, description="Max glob results")
```

#### 6.2 Update `init_store` Call in `main.py`
```python
# In lifespan:
store = MemoryStore(db_path)
await store.migrate_scheduled_tasks_to_slots()
# Pass embed_fn for recall tool
from assistant.backend.pipeline.tool_executor import init_store
init_store(db_path, embed_fn=orchestrator.embed_fn())
```

#### 6.3 Verify `settings.tools_enabled = True` Default
Check `.env.example` and `config.py` have this enabled by default.

### Phase 7: Documentation & Cleanup (Day 4)

#### 7.1 Update `/assistant/AGENTS.md`
Add file sandbox tools section under "Key files" or "Web search":
```
assistant/backend/pipeline/filesystem.py  — sandbox path resolution + safety
assistant/backend/pipeline/tools.py       — tool definitions (incl. file tools)
assistant/backend/pipeline/tool_executor.py — tool executors (incl. file tools)
```

#### 7.2 Run Pre-Commit Flow
```bash
docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/test_files.py -x -q
```

#### 7.3 Manual Verification Checklist
- [ ] Agent creates file: "Write a Python script to calculate fibonacci"
- [ ] Agent reads file: "What's in my fibonacci script?"
- [ ] Agent edits file: "Add a docstring to the fibonacci function"
- [ ] Agent lists files: "What files do I have?"
- [ ] Agent finds files: "Find all .py files"
- [ ] Agent deletes file: "Delete the fibonacci script"
- [ ] CSV handling: "Create a CSV with columns name,email and two rows"
- [ ] Memory integration: "What files have I created?" → recall works

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Model doesn't discover `recall` tool | Medium | High | Strong system prompt examples; tool description emphasizes "search your memory" |
| Path traversal via crafted args | Low | Critical | Centralized `resolve_sandbox_path()` with `relative_to()` check; unit test |
| Symlink escape | Low | High | `validate_path_safety()` resolves symlinks and re-checks |
| Large file DoS | Medium | Medium | Size limits at sandbox layer (10MB write, 1MB read) |
| Memory sync drift | Medium | Medium | Every tool operation updates relevant slots; CSV re-parses on write |
| Tool loop infinite recursion | Low | High | `MAX_TOOL_ROUNDS = 3` limit; `finalize()` required |
| Concurrent file access | Low | Medium | Single-threaded event loop; atomic temp-file writes |

---

## Success Criteria

1. **All 7 tools work**: `write_file`, `read_file`, `edit_file`, `delete_file`, `glob`, `list_files`, `recall`
2. **Model chains tools correctly**: e.g., `glob` → `read_file` → `edit_file` without prompting
3. **Sandbox secure**: Traversal attempts rejected; symlinks validated; size limits enforced
4. **Memory integrated**: Tool-created files discoverable via `list_files`, `recall`, `search_files`
5. **CSV special handling**: Row frames created on write; cascade deleted on parent delete
6. **Tests pass**: `pytest assistant/tests/test_files.py` all green
7. **No regressions**: Full test suite passes; pre-commit flow clean

---

## File Changes Summary

### New Files
- `assistant/backend/pipeline/filesystem.py` — Sandbox path resolution, safety, listing

### Modified Files
- `assistant/backend/pipeline/tools.py` — Add `WriteFileArgs`, `ReadFileArgs`, `EditFileArgs`, `DeleteFileArgs`, `GlobArgs`; update `builtin_tools()`
- `assistant/backend/pipeline/tool_executor.py` — Add executors; fix `execute_recall`; add `_embed_fn` global; update `init_store()`
- `assistant/backend/pipeline/llm_client.py` — Update system prompt with concrete tool examples
- `assistant/backend/config.py` — Add sandbox settings
- `assistant/backend/main.py` — Pass `embed_fn` to `init_store()`
- `assistant/tests/test_files.py` — Add `TestFileSandboxTools` class with 7+ test methods

### No Deleted Files