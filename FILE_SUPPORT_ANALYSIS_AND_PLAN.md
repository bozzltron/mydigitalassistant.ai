# File Support Analysis and Implementation Plan

## Current Implementation Status

### ✅ Backend (Already Implemented)
| Feature | Location | Status |
|---------|----------|--------|
| File upload endpoint (`POST /files/upload`) | `main.py:1459` | ✅ Complete |
| File type validation (.txt, .csv, .json, .xml, .html) | `main.py:1472` | ✅ Complete |
| 10MB size limit | `main.py:1482` | ✅ Complete |
| Content extraction (text, entities, questions) | `files.py:208` | ✅ Complete |
| Memory integration (frames/slots, reliability 0.7) | `main.py:1510` | ✅ Complete |
| File generation (`generate_file`) | `files.py:258` | ✅ Complete |
| Natural language query (`query_content`) | `files.py:20` | ✅ Complete |
| Chat endpoint accepts `attached_files` | `main.py:365, orchestrator.py:116` | ✅ Complete |

### ❌ Missing Backend Features (Agent Integration)
| Feature | Required For | Status |
|---------|--------------|--------|
| Agent file tools (`file_lookup`, `file_read`, `file_write`, `file_update`, `file_delete`, `file_search`) | Phase 3 | ❌ Missing |
| Agent tool registration in `builtin_tools()` | Phase 3 | ❌ Missing |
| File search/list endpoints (`/files/list`, `/files/search`, `/files/{id}`) | UI + Agent | ❌ Missing |
| Download/delete endpoints | UI | ❌ Missing |
| iCalendar (.ics) extractor | Mozworth test case | ❌ Missing |

### ⚠️ Partial UI Implementation (Chat Input)
| Feature | Status | Issues |
|---------|--------|--------|
| File attach button | ✅ Exists | Button styling inconsistent (uses `.btn` but `file-attach-btn` has custom styles) |
| File preview chips | ✅ Exists | Shows below button (vertical stack) instead of horizontally next to it |
| Drag-and-drop | ✅ Exists | Working |
| Send with files | ✅ Exists | Custom `sendForm` function duplicates `sendMessage` logic, doesn't use existing queue/pipeline |
| File in message history | ✅ Shows | User message with file shows, **but typed message doesn't appear in chat thread** |
| Agent access to file | ❌ Doesn't work | Files are stored in memory but agent has no tools to read/query them |

---

## Issues from Manual Testing

### Issue 1: Button Consistency
**Problem**: Send, mic, and file attach buttons have different padding/sizing.
- Send: `padding: 0 1.25rem`, `font-size: 0.95rem`, `font-weight: 500`
- Mic: `padding: 0 0.75rem`, `font-size: 0.9rem` (has `.mic-btn` override)
- File attach: Uses `.btn` class but `file-attach-btn` has custom override `padding: 0 0.75rem`

**Fix**: Standardize all three buttons to use `.btn` class with `.btn-icon` variant for icon-only buttons.

### Issue 2: File Preview Layout
**Problem**: File chips render **below** the input row (vertical), squishing the button area.
- Current: `.file-chips` is inside `.file-attach` div, `flex-wrap: wrap`
- The input row uses `display: flex` with `gap: 0.65rem`

**Fix**: Move file chips to render horizontally next to the attach button, within the same flex row. Use a container that positions chips inline.

### Issue 3: User Message with File Not Showing in Chat
**Problem**: When sending a message with attached files, the user's typed message doesn't appear in chat history.
- Current `sendForm` in chat.html creates file messages via `appendFileMessage()` but doesn't call `addMessage('user', text)` for the text content.
- Uses custom fetch instead of the existing `sendMessage` pipeline.

**Fix**: Use the existing `sendMessage()` function which already handles queueing, loading states, and message history. Just need to include files in the request body.

### Issue 4: Agent Cannot Access Files
**Problem**: Files are uploaded and stored in memory (frames/slots), but the agent has **no tools** to read, search, or manipulate them.
- No `file_lookup`, `file_read`, `file_write` tools in `pipeline/tools.py`
- Agent can't "read the file I just uploaded" or "update the csv"

**Fix**: Add file tools to `pipeline/tools.py` and register in `builtin_tools()`.

### Issue 5: Missing Files Management UI
**Problem**: No dedicated "Files" tab for browsing, viewing, downloading, deleting uploaded files.
- No `/files-ui` route
- No `files.html` 
- No file list/search endpoints

### Issue 6: Missing iCalendar Support
**Problem**: Test case requires `.ics` file support but extractors only handle `.txt`, `.csv`, `.json`, `.xml`, `.html`.

---

## Detailed Implementation Plan

### Phase 1: Fix Chat Input UI (Immediate)

#### 1.1 Button Consistency Fix
Update `chat.html` input row buttons to use shared `.btn` classes consistently:

```html
<!-- Send button - use btn-primary -->
<button id="send-btn" class="btn btn-primary">Send</button>

<!-- Mic button - use btn-icon -->
<button class="btn btn-icon" id="mic-btn" title="Dictate into message box">...</button>

<!-- File attach - use btn-icon -->
<button class="btn btn-icon" id="file-attach-btn" title="Attach file">...</button>
```

Remove custom button styles from chat.html that override `.btn` (lines 504-529, 906-908, 592-605).

#### 1.2 File Chips Horizontal Layout
Change `.file-chips` to render inline in the input row flex container:
- Move `#file-chips-preview` outside `.file-attach` div
- Add it as a flex item in `#input-row` between textarea and buttons
- Use `flex-wrap: nowrap` with `overflow-x: auto` for horizontal scrolling if many files

```html
<div id="input-row">
  <textarea id="msg-input" ...></textarea>
  <div class="file-attach">
    <button class="btn btn-icon" id="file-attach-btn">...</button>
    <input type="file" id="file-input" ...>
  </div>
  <div class="file-chips" id="file-chips-preview"></div>
  <button class="btn btn-icon" id="mic-btn">...</button>
  <button id="send-btn" class="btn btn-primary">Send</button>
</div>
```

Adjust CSS: `.file-chips { margin-left: 0.5rem; flex-shrink: 0; }`

#### 1.3 Fix Send with Files to Use Existing Pipeline
Replace the custom `sendForm` function with integration into existing `sendMessage()`:
- Add `attachedFiles` to the JSON body sent to `/chat`
- The existing `sendMessage()` already handles queueing, loading states, and `addMessage()`
- Remove duplicate `appendFileMessage`, `appendAssistantMessage` from `sendForm`

The `/chat` endpoint already accepts `attached_files` in the request body (line 365 in main.py).

### Phase 2: Backend Agent File Tools (Week 1)

#### 2.1 Add File Tools to `pipeline/tools.py`

```python
# New file tools
async def file_lookup(name_pattern: str, file_type: str = None, user_id: int = 1) -> list[dict]:
    """Search for files by name pattern and/or type. Returns list of file frames."""
    
async def file_read(frame_id: int, user_id: int = 1) -> dict:
    """Read full file content from a file frame. Returns {name, ext, content, slots}."""

async def file_write(name: str, content: str, file_type: str, user_id: int = 1) -> dict:
    """Create a new file frame with content. Returns frame info."""

async def file_update(frame_id: int, new_content: str, user_id: int = 1) -> dict:
    """Update existing file frame content. Returns updated frame info."""

async def file_delete(frame_id: int, user_id: int = 1) -> bool:
    """Delete a file frame."""

async def file_search(query: str, file_type: str = None, user_id: int = 1) -> list[dict]:
    """Semantic search across file content using embeddings."""
```

#### 2.2 Register in `builtin_tools()`
Add file tools to the tool registry so the agent can use them.

#### 2.3 Add .ics Extractor to `files.py`
```python
def extract_text_from_ics(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract events from iCalendar file."""
    # Parse .ics, extract VEVENT components
    # Return plain text summary, entities (event names, dates), questions
```

Add to `extractors` dict in `extract_file_content()`.

#### 2.4 Add File API Endpoints to `main.py`
```python
@app.get("/files/list")
async def list_files(user_id: int = 1, file_type: str = None):
    """List all file frames for user."""

@app.get("/files/search")
async def search_files(q: str, user_id: int = 1, file_type: str = None):
    """Search files by name/content."""

@app.get("/files/{frame_id}")
async def get_file(frame_id: int, user_id: int = 1):
    """Get file details and content."""

@app.get("/files/{frame_id}/content")
async def download_file(frame_id: int, user_id: int = 1):
    """Download file content as text file."""

@app.delete("/files/{frame_id}")
async def delete_file(frame_id: int, user_id: int = 1):
    """Delete file frame."""
```

### Phase 3: Files Management UI (Week 2)

#### 3.1 Create `/files-ui` Route and `files.html`
- New route in `main.py` serving static `files.html`
- Add "Files" nav item in header (chat.html and files.html)

#### 3.2 Files List View (`files.html`)
- Grid of file cards with: icon, name, size, date, source badge
- Actions: View, Copy, Download, Delete
- Filter by type, search by name/content

#### 3.3 File Detail View (Modal)
- Text/Code: syntax highlighted (use existing marked.js or add prism.js)
- JSON: tree view
- CSV: table view
- iCal: formatted events list

### Phase 4: Integration & Testing (Week 2)

#### 4.1 Wire Tools into Agent
- Verify file tools appear in tool list for LLM
- Test agent can: lookup file, read content, update, create new

#### 4.2 iCalendar Mozworth Test Case
```python
# Test: "Let's create a new icalendar for mozworth and add Oct event that is the release of Why Not"
# 1. Agent calls file_lookup("mozworth", ".ics")
# 2. If found: file_read → parse → add VEVENT → file_update
# 3. If not found: generate iCal → file_write
# 4. Verify: file exists with correct VEVENT
```

#### 4.3 Regression Tests
- `test_file_tools_lookup_read_write.py`
- `test_ical_mozworth_creation.py`
- `test_file_upload_then_query.py`

---

## Design Principles Alignment

| Principle | Application |
|-----------|-------------|
| **Model-first correction** | Agent decides file operations via tools, not scripted logic |
| **No templated responses** | Model generates file content (iCal, CSV, JSON) |
| **Lean on model flexibility** | Agent reasons about which file tool to use |
| **Scheduled tasks are memory** | Files stored as frames/slots with embeddings |
| **Clean ship** | No dead code, shared components, lint clean |
| **Stability** | Regression tests for iCal create/update, file upload+query |

---

## Files to Modify

### Backend
1. `assistant/backend/pipeline/tools.py` - Add file tools, register in `builtin_tools()`
2. `assistant/backend/pipeline/files.py` - Add `.ics` extractor
3. `assistant/backend/main.py` - Add file list/search/download/delete endpoints
4. `assistant/backend/static/chat.html` - Fix button consistency, file chips layout, send with files

### Frontend (New)
5. `assistant/backend/static/files.html` - Files management UI
6. `assistant/backend/static/shared/components.css` - Add file card/detail styles

### Tests (New)
7. `assistant/tests/test_file_tools.py`
8. `assistant/tests/test_ical_integration.py`

---

## Quick Wins (Can Do Immediately)

1. **Button consistency**: Update chat.html to use `.btn` / `.btn-icon` classes consistently
2. **Horizontal file chips**: Move `#file-chips-preview` in HTML, adjust CSS
3. **Fix send with files**: Use existing `sendMessage()` with `attached_files` in body
4. **Add .ics extractor**: Small addition to files.py
5. **Delete old plan files**: Remove files in `/plans/` directory as requested