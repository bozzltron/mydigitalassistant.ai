# File Viewer + CSV Experiment Plan
**Complete file management UI + prove agent can speak to CSV rows, edit/delete via prompt, verbatim reference**

---

## Executive Summary

Two workstreams:

1. **File Viewer (P0)**: Finish the file management UI — list, view, delete files. Ensure deletion removes corresponding memory (frame/slots/embeddings). Backend endpoints already exist; UI is missing.
2. **CSV Experiment (P1)**: New experiment to prove the agent can:
   - Upload a CSV and have it stored in memory as structured frames/slots (one frame per row or per column)
   - Speak to any/all rows of data naturally in conversation
   - Edit/delete rows via natural language prompt ("change row 3's status to done", "delete rows where status=cancelled")
   - Reference file contents verbatim in responses
   - Access file contents in the prompt response pipeline (retrieval + context injection)

---

## Current State Analysis

### Backend (Already Implemented)
| Endpoint | Status | Description |
|----------|--------|-------------|
| `POST /files/upload` | ✅ | Upload .txt/.csv/.json/.xml/.html/.ics → extracts content, creates frame, stores slots |
| `GET /files/list` | ✅ | List all file frames for user |
| `GET /files/search?q=` | ✅ | Semantic search over file content |
| `GET /files/{frame_id}` | ✅ | Get file metadata + content |
| `GET /files/{frame_id}/content` | ✅ | Get raw file content |
| `DELETE /files/{frame_id}` | ✅ | Soft-deletes frame (priority=0), removes physical file |

### Memory Model (Already Works)
- File upload creates a frame: `file_<safe_filename>` (type=`entity`)
- Slots: `file_name`, `file_content_preview`, `file_size`, `file_ext`, `file_safe_name`, `entity_<key>`
- For CSV: `extract_text_from_csv()` extracts headers + row entities as `key_entities`
- For ICS: `extract_text_from_ics()` extracts VEVENT summaries, dates as `key_entities`
- Frames have embeddings stored in `frame_embeddings` (sqlite-vec)
- `forget_frame()` soft-deletes (sets priority=0, excludes from retrieval)
- `generate_ical_content()` helper exists in `files.py` (creates single-event ICS)

### Frontend (Partial)
- Chat UI: File attach button, drag-drop, file chips preview, sends files with chat message
- **Missing**: Dedicated file viewer page/tab to browse, view, delete files
- **Missing**: CSV-specific memory representation (row-level frames)
- **Missing**: ICS file creation/editing via prompt, event management, download

### ICS File Management (New Requirement)
The agent should support **calendar file lifecycle** via natural language:
- **Create**: "Create a calendar file for my project meetings" → generates `.ics` with VEVENTs
- **Add events**: "Add a meeting tomorrow 2pm with Alice" → appends VEVENT to existing calendar frame
- **List events**: "What's in my project calendar?" → recalls ICS frame, parses events
- **Edit events**: "Move the Alice meeting to 3pm" → updates VEVENT in frame slots
- **Delete events**: "Remove the cancelled meeting" → removes VEVENT from frame
- **Download**: "Download my calendar" → serves `.ics` file via `/files/{frame_id}/content`

---

## Workstream 1: File Viewer UI (P0)

### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FV-01 | Files tab in sidebar (next to Conversations, Settings) | P0 |
| FV-02 | List all uploaded files: name, type, size, upload date, frame_id | P0 |
| FV-03 | View file content (text, CSV table, JSON tree, .ics events) | P0 |
| FV-04 | Delete file with confirmation dialog | P0 |
| FV-05 | Delete removes frame (soft-delete), slots, embeddings, physical file | P0 |
| FV-06 | Search/filter by type, name, date range | P1 |
| FV-07 | Multi-select + bulk delete | P1 |
| FV-08 | File content accessible to prompt response pipeline (already works via retrieval) | P0 |
| FV-09 | ICS files render as event list with add/edit/delete event buttons | P1 |
| FV-10 | Download button serves raw file via `/files/{frame_id}/content` | P0 |

### UI Components

```
Files Tab (sidebar)
├── Header: "Files" + upload button (reuse existing)
├── Search/filter bar
├── File list (grid or table)
│   ├── File row: icon, name, type badge, size, date, actions
│   └── Actions: View (eye), Delete (trash)
├── Content modal (on View click)
│   ├── Text/JSON/CSV: syntax-highlighted, scrollable
│   ├── CSV: rendered as sortable table
│   ├── .ics: event list with times
│   └── Download button
└── Delete confirmation modal
    ├── "Delete 'filename.csv'? This removes the file and its memory."
    ├── Cancel / Confirm Delete
```

### API Usage (Already Exists)
```javascript
// List files
GET /files/list?user_id=1

// View content
GET /files/{frame_id}/content

// Delete
DELETE /files/{frame_id}  // Returns {status: "ok", message: "File deleted successfully"}
```

### Memory Cleanup on Delete (Verify)
Current `DELETE /files/{frame_id}` in main.py:
1. Gets frame → soft-deletes via `store.forget_frame(frame_id)` (sets priority=0)
2. Removes physical file from `/app/data/`
3. **Gap**: Does NOT clear frame_embeddings, slot_history, associations, episodes linking to frame

**Fix needed**: Extend delete to:
```python
async def delete_file(frame_id):
    # 1. Get file_safe_name for physical file removal
    # 2. Hard-delete or cascade: 
    #    - DELETE FROM frame_embeddings WHERE frame_id = ?
    #    - DELETE FROM slot_history WHERE frame_id = ?
    #    - DELETE FROM associations WHERE from_frame_id = ? OR to_frame_id = ?
    #    - DELETE FROM episodes WHERE frame_ids CONTAINS frame_id (or NULL them)
    #    - DELETE FROM frames WHERE id = ? (or keep soft-delete but clear embeddings)
    # 3. Remove physical file
```

---

## Workstream 2: CSV Experiment (P1)

### Goal
Prove the agent can treat a CSV as **queryable structured memory**, not just a blob of text.

### Experiment Design

#### Phase A: CSV → Structured Memory (Frame per Row)
When a CSV is uploaded, instead of (or in addition to) one frame for the whole file:
- Create a **parent frame**: `file_<name>` (type=`document`)
- Create **child frames**: one per row → `file_<name>_row_<n>` (type=`record`)
- Slots per row frame: each column = slot key, cell value = slot value
- Associations: `parent → part_of → child_row`
- Embeddings: each row frame embedded for semantic search

```python
# Example CSV:
# name,email,status,score
# Alice,alice@example.com,active,95
# Bob,bob@example.com,pending,87

# Creates:
# Frame: file_users_csv (document)
#   Slots: file_name, row_count=2, columns=["name","email","status","score"]
# Frame: file_users_csv_row_1 (record)
#   Slots: name="Alice", email="alice@example.com", status="active", score="95"
#   Association: file_users_csv --part_of--> file_users_csv_row_1
# Frame: file_users_csv_row_2 (record)
#   Slots: name="Bob", email="bob@example.com", status="pending", score="87"
```

#### Phase B: Natural Language Row Operations
Agent should handle prompts like:
- "Show me all active users" → recalls rows where status=active
- "What's Bob's score?" → recalls row_2, returns score slot
- "Change Alice's status to inactive" → upsert_slot on row_1, key=status, value=inactive
- "Delete rows where score < 90" → forget_frame on matching row frames
- "Add a new user: Carol, carol@example.com, active, 92" → create new row frame
- "Export as CSV" → agent generates CSV from row frames

#### Phase C: Verbatim Reference + Pipeline Access
- **Verbatim**: "Row 3 says: name=Carol, email=carol@example.com, status=active, score=92"
- **Pipeline**: File content retrieved via `recall()` tool, injected into system prompt as structured context

### Technical Approach

#### 1. Enhanced CSV Extraction (`files.py`)
```python
def extract_text_from_csv(content: bytes) -> tuple[str, list[str], list[str], list[dict]]:
    """
    Returns: (plain_text, key_entities, open_questions, row_data)
    row_data = [{"name": "Alice", "email": "...", "status": "active", "score": "95"}, ...]
    """
```

#### 2. Enhanced Upload Endpoint (`main.py`)
```python
# After extracting row_data:
parent_frame = await store.create_frame(
    f"file_{safe_filename}", "document", 
    source_type="file_upload", owner_user_id=user_id
)

# Store CSV metadata on parent
await store.upsert_slot(parent_frame.id, "columns", json.dumps(headers), ...)
await store.upsert_slot(parent_frame.id, "row_count", str(len(row_data)), ...)

# Create row frames
for i, row in enumerate(row_data):
    row_frame = await store.create_frame(
        f"file_{safe_filename}_row_{i+1}", "record",
        source_type="file_upload", owner_user_id=user_id
    )
    for col, val in row.items():
        await store.upsert_slot(row_frame.id, col, str(val), ...)
    # Link parent → child
    await store.create_association(parent_frame.id, row_frame.id, "part_of")
```

#### 3. Retrieval Integration
- Row frames are normal frames → `recall()` tool finds them via embedding similarity
- Query "active users" → embedding matches "status=active" rows
- Parent frame retrieved for context (column names, row count)

#### 4. LLM Tool Access
Agent already has `upsert_slot`, `recall`, `forget_frame` tools → can edit/delete rows naturally

---

## Implementation Plan

### Phase 1: File Viewer UI (Week 1)

| Step | Task | File | Effort |
|------|------|------|--------|
| 1.1 | Add Files tab to sidebar navigation | `chat.html` | 0.5 day |
| 1.2 | Create file list view component | `chat.html` / new `files.js` | 1 day |
| 1.3 | Create file content modal (text/CSV/JSON/.ics) | `chat.html` | 1 day |
| 1.4 | Wire delete button → DELETE /files/{frame_id} | `chat.html` | 0.5 day |
| 1.5 | Backend: enhance delete to clean embeddings/associations | `main.py` | 0.5 day |
| 1.6 | Test: upload → view → delete → verify memory gone | - | 0.5 day |

### Phase 2: CSV Structured Memory (Week 2)

| Step | Task | File | Effort |
|------|------|------|--------|
| 2.1 | Enhance `extract_text_from_csv` to return row_data | `files.py` | 0.5 day |
| 2.2 | Modify `/files/upload` to create parent + row frames | `main.py` | 1 day |
| 2.3 | Add CSV table renderer in file content modal | `chat.html` | 0.5 day |
| 2.4 | Test: upload CSV → verify row frames in memory | - | 0.5 day |

### Phase 3: Natural Language Row Ops (Week 3)

| Step | Task | File | Effort |
|------|------|------|--------|
| 3.1 | Test agent can recall rows via `recall()` tool | - | 0.5 day |
| 3.2 | Test agent can edit rows via `upsert_slot` | - | 0.5 day |
| 3.3 | Test agent can delete rows via `forget_frame` | - | 0.5 day |
| 3.4 | Test verbatim reference in responses | - | 0.5 day |
| 3.5 | Test pipeline access: file content in context | - | 0.5 day |

### Phase 4: Experiment Validation (Week 3-4)

| Step | Task | Effort |
|------|------|--------|
| 4.1 | Create test CSV (100 rows, mixed types) | 0.5 day |
| 4.2 | Run evaluation scenarios (see below) | 1 day |
| 4.3 | Document results, iterate on extraction | 1 day |
| 4.4 | Write experiment report | 0.5 day |

### Phase 5: ICS Calendar File Management (Week 4-5)

| Step | Task | File | Effort |
|------|------|------|--------|
| 5.1 | Enhance `extract_text_from_ics` to return structured events | `files.py` | 0.5 day |
| 5.2 | Add `create_ics_file` tool (generate + store) | `tools.py` / `tool_executor.py` | 1 day |
| 5.3 | Add `add_ics_event` / `update_ics_event` / `delete_ics_event` tools | `tools.py` / `tool_executor.py` | 1 day |
| 5.4 | ICS event renderer in file content modal (add/edit/delete UI) | `chat.html` / `files.js` | 1 day |
| 5.5 | Test: create calendar → add events → edit → download → verify | - | 0.5 day |

---

## Evaluation Scenarios (CSV Experiment)

| # | Scenario | Expected Behavior | Pass Criteria |
|---|----------|-------------------|---------------|
| 1 | Upload `users.csv` (100 rows) | Parent + 100 row frames created, embedded | Frame count = 101 |
| 2 | "How many active users?" | Agent recalls, counts status=active rows | Correct count |
| 3 | "Show me Alice's email" | Agent recalls row_1, returns email slot | Correct email |
| 4 | "List all users with score > 90" | Agent recalls, filters, lists names | Correct list |
| 5 | "Change Bob's status to active" | Agent upserts row_2 status=active | Slot updated |
| 6 | "Delete rows where status=cancelled" | Agent forgets matching row frames | Frames priority=0 |
| 7 | "Add user: Dave, dave@x.com, active, 88" | Agent creates new row frame | Row frame exists |
| 8 | "What does row 5 say verbatim?" | Agent quotes all slots for row_5 | Exact match |
| 9 | "Export filtered users as CSV" | Agent generates CSV from row frames | Valid CSV output |
| 10 | "Summarize this CSV" | Agent uses retrieval + LLM summary | Meaningful summary |

---

## Evaluation Scenarios (ICS Calendar Experiment)

| # | Scenario | Expected Behavior | Pass Criteria |
|---|----------|-------------------|---------------|
| 1 | "Create a calendar for project meetings" | Agent calls `create_ics_file`, stores frame with VEVENTs | Frame created, type=calendar, events parseable |
| 2 | "Add meeting tomorrow 2pm with Alice" | Agent calls `add_ics_event` on calendar frame | VEVENT added with DTSTART, SUMMARY, UID |
| 3 | "Add recurring standup Mon-Fri 9am" | Agent adds VEVENT with RRULE | RRULE=FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR |
| 4 | "What's in my project calendar?" | Agent recalls calendar frame, lists events | All events listed with time, summary |
| 5 | "Move Alice meeting to 3pm" | Agent calls `update_ics_event` (match by summary) | DTSTART updated, UID preserved |
| 6 | "Cancel the standup on Wednesday" | Agent calls `delete_ics_event` (match by date/summary) | VEVENT removed from frame |
| 7 | "Download my calendar" | Agent provides download link / serves `/files/{id}/content` | Valid .ics file downloads, imports to Google/Outlook |
| 8 | "Export calendar as ICS" | Agent generates ICS from frame slots | Valid ICS with all VEVENTs |
| 9 | Verbatim: "What does the Alice event say?" | Agent quotes DTSTART, DTEND, SUMMARY, DESCRIPTION | Exact match from frame slots |
| 10 | "Show meetings this week" | Agent filters events by date range | Correct subset returned |

---

## Success Metrics

### File Viewer
- [ ] User can upload, view, delete files from UI
- [ ] Delete removes frame (priority=0), embeddings, associations, physical file
- [ ] No memory leaks: deleted files don't appear in `recall()` or `/memory/search`

### CSV Experiment
- [ ] 100-row CSV → 101 frames (1 parent + 100 rows), all embedded
- [ ] Recall latency < 500ms for 100-row CSV
- [ ] Agent answers 9/10 evaluation scenarios correctly
- [ ] Verbatim reference works: agent quotes exact cell values
- [ ] Edit/delete via prompt persists in memory (survives restart)
- [ ] No regression on existing file upload / chat tests

### ICS Calendar Experiment
- [ ] Calendar frame created with type=calendar, events as VEVENT slots
- [ ] Add/edit/delete events via prompt updates frame correctly
- [ ] RRULE recurring events parsed and stored
- [ ] Download serves valid .ics (imports to Google Calendar, Outlook, Apple Calendar)
- [ ] Verbatim reference quotes exact event fields
- [ ] Date range queries filter events correctly
- [ ] No regression on existing file upload / chat tests

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Row frames pollute memory (too many) | Medium | Medium | Add `source_type="csv_row"` filter; GC respects `essential`; user can bulk delete |
| Embedding 100 rows slow | Low | Medium | Batch embed; async; progress indicator |
| Agent hallucinates row data | Medium | High | Verbatim reference forces exact slot values; retrieval returns actual slots |
| CSV with 10k rows → 10k frames | Low | High | Add row limit (configurable, default 1000); stream large CSVs |
| Column names with spaces/special chars | Medium | Low | Normalize slot keys: `snake_case`, strip special chars |
| ICS timezone handling errors | Medium | High | Store DTSTART/DTEND in UTC with TZID; use `dateutil` for parsing |
| RRULE parsing complexity | Medium | Medium | Support common RRULEs (DAILY, WEEKLY, MONTHLY); delegate complex to LLM |
| UID collision on event edit | Low | High | Preserve original UID; generate new only on create |
| Invalid ICS output | Medium | High | Validate with `icalendar` library before serving; round-trip test |

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Row frames pollute memory (too many) | Medium | Medium | Add `source_type="csv_row"` filter; GC respects `essential`; user can bulk delete |
| Embedding 100 rows slow | Low | Medium | Batch embed; async; progress indicator |
| Agent hallucinates row data | Medium | High | Verbatim reference forces exact slot values; retrieval returns actual slots |
| CSV with 10k rows → 10k frames | Low | High | Add row limit (configurable, default 1000); stream large CSVs |
| Column names with spaces/special chars | Medium | Low | Normalize slot keys: `snake_case`, strip special chars |

---

## Dependencies

- Existing: `files.py` extraction, `main.py` endpoints, `MemoryStore` CRUD, `recall`/`upsert_slot`/`forget_frame` tools
- New: File viewer UI components, enhanced CSV extraction, row frame creation logic
- Testing: `pytest assistant/tests/test_files.py` (new), `pytest assistant/tests/test_csv_experiment.py` (new)

---

## Rollout

| Stage | Scope |
|-------|-------|
| 1 | File viewer UI behind feature flag `FILE_VIEWER_ENABLED` |
| 2 | CSV structured memory behind `CSV_ROW_FRAMES_ENABLED` |
| 3 | Default on after experiment validation |

---

## Appendix: File Delete Memory Cleanup (Backend Change)

```python
# In main.py DELETE /files/{frame_id} - enhanced version
@app.delete("/files/{frame_id}")
async def delete_file(frame_id: int, store: MemoryStore = _Depends(get_store)):
    frame = await store.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="File not found")

    # Get file_safe_name for physical file removal
    async with store._connect() as db:
        row = await db.execute_fetchone(
            "SELECT value FROM slots WHERE frame_id = ? AND key = 'file_safe_name'",
            (frame_id,),
        )
        file_safe_name = row[0] if row else None

    # Cascade cleanup memory
    async with store._connect() as db:
        # 1. Clear embeddings
        await db.execute("DELETE FROM frame_embeddings WHERE frame_id = ?", (frame_id,))
        # 2. Clear slot history
        await db.execute("DELETE FROM slot_history WHERE frame_id = ?", (frame_id,))
        # 3. Clear associations (both directions)
        await db.execute("DELETE FROM associations WHERE from_frame_id = ? OR to_frame_id = ?", (frame_id, frame_id))
        # 4. Remove frame_id from episodes' frame_ids JSON array
        await db.execute("""
            UPDATE episodes 
            SET frame_ids = (
                SELECT json_group_array(value) 
                FROM json_each(frame_ids) 
                WHERE value != ?
            )
            WHERE json_type(frame_ids) = 'array' AND EXISTS (
                SELECT 1 FROM json_each(frame_ids) WHERE value = ?
            )
        """, (frame_id, frame_id))
        # 5. Hard delete frame (or keep soft-delete but cascade slots)
        await db.execute("DELETE FROM frames WHERE id = ?", (frame_id,))
        await db.commit()

    # Remove physical file
    if file_safe_name:
        file_path = Path("/app/data") / file_safe_name
        if file_path.exists():
            file_path.unlink(missing_ok=True)

    return {"status": "ok", "message": "File and associated memory deleted"}
```

---

## Appendix: CSV Extraction Enhancement

```python
# In files.py - enhanced extract_text_from_csv
def extract_text_from_csv(content: bytes) -> tuple[str, list[str], list[str], list[dict]]:
    try:
        text = content.decode("utf-8", errors="replace")
        lines = text.strip().split("\n")
        key_entities = []
        open_questions = []
        row_data = []

        if lines:
            headers = [h.strip() for h in lines[0].split(",")]
            for i, line in enumerate(lines[1:], 1):
                values = [v.strip() for v in line.split(",")]
                row = dict(zip(headers, values))
                row_data.append(row)
                # Entities from row
                for header, value in row.items():
                    if value and value not in ("NA", "N/A", "", "null"):
                        entity_key = f"{header}_{value}"
                        if entity_key not in key_entities:
                            key_entities.append(entity_key)
            if len(lines) > 1:
                open_questions.append("Review data for patterns and insights")
        plain_text = "\n".join(lines)
        return plain_text, key_entities, open_questions, row_data
    except Exception:
        return "", [], [], []
```

---

## Notes for Implementing Agent

1. **Start with Workstream 1** (File Viewer) — it's purely UI + minor backend cleanup. Gets immediate user value.
2. **Workstream 2** (CSV Experiment) builds on the memory model — no new infrastructure needed, just enhanced extraction and frame creation.
3. **Test incrementally**: After Phase 2, verify row frames exist in Brain Observatory (`/brain-ui`).
4. **Use existing tools**: Agent already has `recall`, `upsert_slot`, `forget_frame` — the experiment proves they work for structured data.
5. **Keep it local-first**: All processing in `/app/data`, all inference via Ollama, no cloud calls.

---

## Detailed Technical Specifications (For Implementation)

### File Viewer UI - Component Architecture

```javascript
// static/shared/files.js (new module)
export class FileViewer {
    constructor(api, toast) {
        this.api = api;
        this.toast = toast;
        this.files = [];
        this.filteredFiles = [];
        this.currentFilter = { type: '', search: '', dateFrom: '', dateTo: '' };
        this.selectedFiles = new Set();
    }

    async load() {
        const resp = await this.api('/files/list?user_id=1');
        this.files = resp;
        this.applyFilters();
        this.render();
    }

    applyFilters() {
        this.filteredFiles = this.files.filter(f => {
            if (this.filter.type && f.file_ext !== this.filter.type) return false;
            if (this.filter.search && !f.file_name.toLowerCase().includes(this.filter.search.toLowerCase())) return false;
            if (this.filter.dateFrom && new Date(f.created_at) < new Date(this.filter.dateFrom)) return false;
            if (this.filter.dateTo && new Date(f.created_at) > new Date(this.filter.dateTo)) return false;
            return true;
        });
    }

    render() {
        // Render file grid/table with selection checkboxes
    }

    async viewFile(frameId) {
        const resp = await this.api(`/files/${frameId}/content`);
        this.showContentModal(resp);
    }

    async deleteFiles(frameIds) {
        if (!confirm(`Delete ${frameIds.length} file(s)? This removes files and their memory.`)) return;
        for (const id of frameIds) {
            await this.api(`/files/${id}`, { method: 'DELETE' });
        }
        this.toast(`Deleted ${frameIds.length} file(s)`, 'success');
        await this.load();
    }

    showContentModal(fileData) {
        // Modal with syntax highlighting for text/JSON, table for CSV, events for .ics
    }
}
```

### File Content Renderers

```javascript
// static/shared/file-renderers.js (new module)
export const FileRenderers = {
    txt: (content) => `<pre class="file-content-text">${escapeHtml(content)}</pre>`,
    
    csv: (content) => {
        const lines = content.trim().split('\n');
        const headers = lines[0].split(',').map(h => h.trim());
        const rows = lines.slice(1).map(l => l.split(',').map(v => v.trim()));
        return `
            <div class="csv-table-container">
                <table class="csv-table">
                    <thead><tr>${headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead>
                    <tbody>
                        ${rows.map(r => `<tr>${r.map(v => `<td>${escapeHtml(v)}</td>`).join('')}</tr>`).join('')}
                    </tbody>
                </table>
            </div>
        `;
    },
    
    json: (content) => `<pre class="file-content-json">${syntaxHighlightJson(content)}</pre>`,
    
    ics: (content) => {
        // Parse VEVENT components, render as event cards
        const events = parseIcs(content);
        return events.map(e => `
            <div class="ics-event">
                <h4>${escapeHtml(e.summary)}</h4>
                <p>${formatDate(e.dtstart)} - ${formatDate(e.dtend)}</p>
                ${e.description ? `<p>${escapeHtml(e.description)}</p>` : ''}
            </div>
        `).join('');
    },
    
    html: (content) => `<div class="file-content-html">${content}</div>`,
};
```

### Backend - Enhanced Delete with Cascade

```python
# assistant/backend/main.py - replace existing DELETE /files/{frame_id}
@app.delete("/files/{frame_id}")
async def delete_file(
    frame_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Delete a file frame and ALL associated memory + physical file."""
    frame = await store.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="File not found")

    # Get file_safe_name for physical file removal
    async with store._connect() as db:
        row = await db.execute_fetchone(
            "SELECT value FROM slots WHERE frame_id = ? AND key = 'file_safe_name'",
            (frame_id,),
        )
        file_safe_name = row[0] if row else None

        # CASCADE CLEANUP - single transaction
        # 1. Clear frame embeddings
        await db.execute("DELETE FROM frame_embeddings WHERE frame_id = ?", (frame_id,))
        
        # 2. Clear slot history for this frame's slots
        await db.execute("""
            DELETE FROM slot_history 
            WHERE slot_id IN (SELECT id FROM slots WHERE frame_id = ?)
        """, (frame_id,))
        
        # 3. Clear associations (both directions)
        await db.execute(
            "DELETE FROM associations WHERE from_frame_id = ? OR to_frame_id = ?",
            (frame_id, frame_id)
        )
        
        # 4. Remove frame_id from episodes' frame_ids JSON arrays
        await db.execute("""
            UPDATE episodes 
            SET frame_ids = (
                SELECT json_group_array(value) 
                FROM json_each(frame_ids) 
                WHERE CAST(value AS INTEGER) != ?
            )
            WHERE json_type(frame_ids) = 'array' AND EXISTS (
                SELECT 1 FROM json_each(frame_ids) WHERE CAST(value AS INTEGER) = ?
            )
        """, (frame_id, frame_id))
        
        # 5. Delete slots (CASCADE from frames FK handles this, but explicit is clearer)
        await db.execute("DELETE FROM slots WHERE frame_id = ?", (frame_id,))
        
        # 6. Hard delete the frame
        await db.execute("DELETE FROM frames WHERE id = ?", (frame_id,))
        
        await db.commit()

    # Remove physical file
    if file_safe_name:
        file_path = Path("/app/data") / file_safe_name
        if file_path.exists():
            file_path.unlink(missing_ok=True)

    return {"status": "ok", "message": "File and associated memory deleted"}
```

### CSV Enhanced Extraction (files.py)

```python
# assistant/backend/pipeline/files.py - replace extract_text_from_csv
def extract_text_from_csv(content: bytes) -> tuple[str, list[str], list[str], list[dict]]:
    """
    Extract structured data from CSV.
    
    Returns:
        plain_text: Full CSV as text
        key_entities: Unique header_value pairs
        open_questions: Heuristic questions
        row_data: List of dicts, one per row {column: value}
    """
    try:
        text = content.decode("utf-8", errors="replace")
        lines = [l for l in text.strip().split("\n") if l.strip()]
        
        if not lines:
            return "", [], [], []
        
        # Parse headers (handle quoted fields)
        import csv
        from io import StringIO
        
        reader = csv.reader(StringIO(text))
        all_rows = list(reader)
        
        if not all_rows:
            return "", [], [], []
        
        headers = [h.strip() for h in all_rows[0]]
        row_data = []
        key_entities = []
        
        for row_vals in all_rows[1:]:
            # Pad row if fewer columns than headers
            row_vals = list(row_vals) + [""] * (len(headers) - len(row_vals))
            row = {headers[i]: row_vals[i].strip() for i in range(len(headers))}
            row_data.append(row)
            
            # Extract entities
            for header, value in row.items():
                if value and value not in ("NA", "N/A", "", "null", "None"):
                    entity_key = f"{header}_{value}"
                    if entity_key not in key_entities:
                        key_entities.append(entity_key)
        
        open_questions = []
        if len(row_data) > 0:
            open_questions.append(f"Analyze {len(row_data)} rows for patterns and insights")
            # Detect potential ID columns
            for h in headers:
                if h.lower() in ('id', 'uuid', 'key', 'pk'):
                    open_questions.append(f"Column '{h}' appears to be an identifier")
        
        plain_text = "\n".join(",".join(r) for r in all_rows)
        return plain_text, key_entities, open_questions, row_data
        
    except Exception as e:
        logger.warning(f"CSV extraction failed: {e}")
        return "", [], [], []
```

### CSV Upload - Parent + Row Frames (main.py)

```python
# assistant/backend/main.py - modify /files/upload endpoint after extraction
# ... existing code up to extraction_result = await extract_file_content(...)

# NEW: Check if CSV and create row frames
row_data = extraction_result.get("row_data", []) if isinstance(extraction_result, dict) else []

if ext == "csv" and row_data:
    # Create parent document frame
    parent_frame_name = f"file_{safe_filename}"
    parent_frame = await store.create_frame(
        parent_frame_name, "document",
        source_type="file_upload", owner_user_id=user_id,
        source_reliability=0.8,
        description=f"CSV file: {filename}, {len(row_data)} rows"
    )
    
    # Store CSV metadata on parent
    headers = list(row_data[0].keys()) if row_data else []
    await store.upsert_slot(parent_frame.id, "columns", json.dumps(headers), source_type="file_upload", source_reliability=0.9)
    await store.upsert_slot(parent_frame.id, "row_count", str(len(row_data)), source_type="file_upload", source_reliability=0.9)
    await store.upsert_slot(parent_frame.id, "file_name", filename, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(parent_frame.id, "file_ext", ext, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(parent_frame.id, "file_size", str(len(content)), source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(parent_frame.id, "file_safe_name", safe_filename, source_type="file_upload", source_reliability=0.7)
    
    # Create row frames (batch for performance)
    row_frame_ids = []
    for i, row in enumerate(row_data):
        row_frame_name = f"file_{safe_filename}_row_{i+1}"
        row_frame = await store.create_frame(
            row_frame_name, "record",
            source_type="csv_row", owner_user_id=user_id,
            source_reliability=0.85,
            description=f"Row {i+1} of {filename}"
        )
        row_frame_ids.append(row_frame.id)
        
        # Store each column as a slot
        for col, val in row.items():
            # Normalize slot key: snake_case, alphanumeric + underscore
            slot_key = re.sub(r'[^a-zA-Z0-9_]', '_', col.lower().strip())
            slot_key = re.sub(r'_+', '_', slot_key).strip('_')
            if not slot_key:
                slot_key = f"col_{headers.index(col)}"
            
            await store.upsert_slot(
                row_frame.id, slot_key, str(val),
                source_type="csv_row", source_reliability=0.85,
                priority=0.5
            )
        
        # Link parent -> row (part_of)
        await store.create_association(parent_frame.id, row_frame.id, "part_of", confidence=0.9)
    
    # Embed all row frames (async, best-effort)
    asyncio.create_task(store.embed_frames(row_frame_ids, llm_client.embed))
    
    # Update response to include row frame info
    return {
        "status": "ok",
        "file_name": filename,
        "file_size": len(content),
        "file_ext": ext,
        "row_count": len(row_data),
        "columns": headers,
        "parent_frame_id": parent_frame.id,
        "row_frame_ids": row_frame_ids,
        "key_entities": extraction_result.get("key_entities", []),
        "open_questions": extraction_result.get("open_questions", []),
    }

# ... existing non-CSV handling
```

### Retrieval Integration - CSV Row Frames in Context

The existing `recall()` tool in `tools.py` already searches all frames by embedding similarity. Row frames (type=`record`) will be found naturally. To improve CSV-specific retrieval:

```python
# assistant/backend/pipeline/tools.py - enhance recall tool handler
async def _handle_recall(args, user_id, store, llm_client):
    query = args.get("query", "")
    frame_types = args.get("frame_types")  # Can filter by type
    
    # If query mentions "row", "csv", "spreadsheet", "record" - boost record frames
    boost_record = any(term in query.lower() for term in ["row", "csv", "spreadsheet", "record", "entry"])
    
    results = await store.search_similar_frames(
        embedding=await llm_client.embed(query),
        user_id=user_id,
        limit=args.get("max_results", 10),
        min_distance=1.0 - args.get("min_confidence", 0.3),
    )
    
    # Filter by frame_types if specified
    if frame_types:
        results = [(f, s, sim) for f, s, sim in results if f.type in frame_types]
    
    # Boost record frames for CSV queries
    if boost_record:
        results = sorted(results, key=lambda x: (x[0].type != "record", x[2]), reverse=True)
    
    return {
        "frames": [
            {
                "id": f.id, "name": f.name, "type": f.type,
                "slots": [{"key": s.key, "value": s.value, "confidence": s.confidence} for s in slots],
                "similarity": round(sim, 3)
            }
            for f, slots, sim in results
        ]
    }
```

### Test Specifications

```python
# assistant/tests/test_files.py (new)
import pytest
from assistant.backend.main import app
from assistant.backend.memory.store import MemoryStore

class TestFileViewer:
    """File viewer UI backend tests."""
    
    @pytest.mark.asyncio
    async def test_list_files(self, store: MemoryStore):
        # Upload test files
        # Call GET /files/list
        # Verify response structure
        pass
    
    @pytest.mark.asyncio
    async def test_view_file_content(self, store: MemoryStore):
        # Upload file
        # Call GET /files/{frame_id}/content
        # Verify content matches
        pass
    
    @pytest.mark.asyncio
    async def test_delete_file_cascades(self, store: MemoryStore):
        # Upload file
        # Verify frame, slots, embeddings, associations exist
        # Call DELETE /files/{frame_id}
        # Verify frame deleted (hard or priority=0)
        # Verify embeddings gone
        # Verify slot_history gone
        # Verify associations gone
        # Verify frame_id removed from episodes
        # Verify physical file gone
        pass
    
    @pytest.mark.asyncio
    async def test_delete_csv_removes_row_frames(self, store: MemoryStore):
        # Upload CSV (creates parent + row frames)
        # Delete parent
        # Verify all row frames also deleted
        pass


# assistant/tests/test_csv_experiment.py (new)
class TestCSVExperiment:
    """CSV structured memory experiment tests."""
    
    @pytest.mark.asyncio
    async def test_csv_creates_parent_and_row_frames(self, store: MemoryStore, llm_client):
        csv_content = b"name,email,status\nAlice,a@b.com,active\nBob,b@c.com,pending"
        # Upload via /files/upload
        # Verify 1 parent frame (type=document) + 2 row frames (type=record)
        # Verify slots on row frames match columns
        # Verify part_of associations parent->rows
        pass
    
    @pytest.mark.asyncio
    async def test_recall_finds_csv_rows(self, store: MemoryStore, llm_client):
        # Upload CSV with 10 rows
        # Query "active users" via recall tool
        # Verify row frames with status=active returned
        pass
    
    @pytest.mark.asyncio
    async def test_agent_can_edit_row_via_prompt(self, store: MemoryStore, llm_client, orchestrator):
        # Upload CSV
        # Send chat: "Change Alice's status to inactive"
        # Verify row frame slot updated
        pass
    
    @pytest.mark.asyncio
    async def test_agent_can_delete_rows_via_prompt(self, store: MemoryStore, llm_client, orchestrator):
        # Upload CSV
        # Send chat: "Delete rows where status=cancelled"
        # Verify matching row frames forgotten (priority=0)
        pass
    
    @pytest.mark.asyncio
    async def test_verbatim_reference(self, store: MemoryStore, llm_client, orchestrator):
        # Upload CSV
        # Send chat: "What does row 3 say verbatim?"
        # Verify response quotes exact slot values
        pass
    
    @pytest.mark.asyncio
    async def test_pipeline_access_file_content(self, store: MemoryStore, llm_client, orchestrator):
        # Upload CSV
        # Send chat: "Summarize the CSV"
        # Verify response uses retrieved row data
        pass
    
    @pytest.mark.asyncio
    async def test_large_csv_performance(self, store: MemoryStore, llm_client):
        # Upload 500-row CSV
        # Measure frame creation time < 10s
        # Measure recall latency < 500ms
        pass
```

### Configuration (`.env` additions)

```bash
# File viewer
FILE_VIEWER_ENABLED=true

# CSV structured memory
CSV_ROW_FRAMES_ENABLED=true
CSV_MAX_ROWS=1000          # Safety limit for row frames
CSV_EMBED_BATCH_SIZE=50    # Batch size for embedding row frames
```

### UI Integration Points (chat.html)

Add to sidebar navigation (after Settings button, before Brain link):
```html
<button id="files-tab-btn" class="nav-link" title="Files" style="color:var(--text-dim);text-decoration:none;font-size:0.8rem;padding:0.35rem 0.75rem;border:1px solid var(--border);border-radius:6px;">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
  Files
</button>
```

Add Files panel (hidden by default, shown when Files tab clicked):
```html
<div id="files-panel" class="sidebar-panel hidden">
  <div class="panel-header">
    <h3>Files</h3>
    <button class="panel-close">&times;</button>
  </div>
  <div class="panel-toolbar">
    <input type="search" id="files-search" placeholder="Search files...">
    <select id="files-filter-type">
      <option value="">All types</option>
      <option value="csv">CSV</option>
      <option value="json">JSON</option>
      <option value="txt">Text</option>
      <option value="xml">XML</option>
      <option value="html">HTML</option>
      <option value="ics">Calendar</option>
    </select>
  </div>
  <div id="files-list" class="files-grid"></div>
  <div id="files-selected-bar" class="hidden">
    <span id="files-selected-count">0 selected</span>
    <button id="files-bulk-delete" class="btn-danger">Delete Selected</button>
  </div>
</div>
```

---

## Acceptance Checklist for Handoff

### File Viewer (P0)
- [ ] Files tab appears in sidebar
- [ ] Clicking Files tab loads `/files/list` and renders grid
- [ ] Search/filter works (name, type, date)
- [ ] Clicking file opens content modal with correct renderer
- [ ] Delete button shows confirmation, calls DELETE, refreshes list
- [ ] Bulk delete works (multi-select + delete)
- [ ] Backend delete cascades: embeddings, slot_history, associations, episodes, slots, frame, physical file
- [ ] No memory leaks verified via `/memory/search` and Brain Observatory

### CSV Experiment (P1)
- [ ] CSV upload creates parent (document) + row (record) frames
- [ ] Row frames have slots per column, normalized keys
- [ ] Parent has columns, row_count slots
- [ ] Parent→row associations (part_of) created
- [ ] Row frames embedded (async, verify in Brain Observatory)
- [ ] Agent recalls rows via `recall()` tool
- [ ] Agent edits row slot via `upsert_slot` from prompt
- [ ] Agent deletes rows via `forget_frame` from prompt
- [ ] Agent references row verbatim in response
- [ ] Agent summarizes CSV using retrieved rows
- [ ] 10/10 evaluation scenarios pass
- [ ] Performance: 100 rows < 500ms recall, 500 rows < 10s upload
- [ ] No regression: existing file upload, chat, correction tests pass

### Security & Privacy
- [ ] File access scoped to user_id (owner_user_id on frames)
- [ ] No path traversal in file serving
- [ ] Delete confirms ownership before cascade
- [ ] CSV row frames respect same user isolation

---

## Handoff Notes

**Start here**: `assistant/backend/main.py` line 1829 (`DELETE /files/{frame_id}`) — fix the cascade delete first. Then `assistant/backend/pipeline/files.py` line 49 (`extract_text_from_csv`) — enhance extraction. Then `assistant/backend/main.py` line 1459 (`/files/upload`) — add row frame creation.

**UI entry point**: `assistant/backend/static/chat.html` — add Files tab, panel, and wire to new `static/shared/files.js` module.

**Test first**: Write `test_files.py` and `test_csv_experiment.py` before implementing — they define the contract.

**Debug tools**: Use `/brain-ui` (Brain Observatory) to visually verify frames, slots, associations, embeddings after each step. Use `assistant db backup` / `restore` for safety.

**Model constraints**: All LLM calls via local Ollama (127.0.0.1:11434). No external APIs. SearXNG for search if needed.