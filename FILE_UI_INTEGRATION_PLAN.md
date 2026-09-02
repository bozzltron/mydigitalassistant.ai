# File UI Integration Plan

## Overview
Wire up file input/output capabilities to the chat UI and add a dedicated Files management tab.

## Phase 1: Chat Input File Selector (chat.html)

### 1.1 File Attachment Button in Chat Input
- Add file attachment button (📎) in the chat input toolbar
- Support multiple file uploads per message
- Show attached files as removable chips before sending
- File preview thumbnails for images

### 1.2 Drag-and-Drop Support
- Drag-and-drop files onto chat input area
- Visual feedback during drag (highlight drop zone)

### 1.3 Backend Integration
- Send files as `multipart/form-data` with message
- Modify `/chat` endpoint to accept `files` field
- Store file IDs with message for context

---

## Phase 2: Files Management Tab (New files.html)

### 2.1 New Files Tab
- Add "Files" navigation item in header (next to Chat, Brain)
- Route: `/files-ui` → serves `files.html`

### 2.2 Files List View (`files.html`)
- **File List**: Grid of all user files with:
  - File type icon (📄 txt, 📊 csv, 📋 json, 📰 xml, 🌐 html, 📅 ics)
  - File name (clickable → detail view)
  - File size, upload date
  - Source badge (uploaded, generated, extracted)
- **Actions per file**: View, Copy content, Download, Delete
- **Bulk actions**: Select multiple, delete, download zip

### 2.3 File Detail View (Modal/Page)
- **Text/Code files**: Syntax-highlighted content viewer
- **Images**: Full-size preview
- **Structured files**: Formatted viewer (JSON tree, CSV table, iCal events)
- **Actions**: Copy, Download, Edit (for text), Delete

### 2.4 Search & Filter
- Filter by file type, date range, source
- Search by filename or content

---

## Phase 3: Agent File Reference Integration (Backend)

### 3.1 New File Tools for Agent
```python
# New tools in pipeline/tools.py
async def file_lookup(name_pattern: str, file_type: str = None) -> list[FileInfo]
async def file_read(file_id: int) -> FileContent
async def file_write(name: str, content: str, file_type: str) -> FileFrame
async def file_update(file_id: int, new_content: str) -> FileFrame
```

### 3.2 Agent Integration Logic
- File tools registered in `builtin_tools()`
- Tool descriptions guide agent to use files for:
  - iCalendar management (.ics files)
  - Structured data (CSV, JSON)
  - Document templates
  - Reference materials

### 3.3 Natural Language File Reference
- "mozworth ical" → searches for `.ics` files with "mozworth" in name/content
- "the csv we uploaded" → finds recent CSV files
- "the ical for mozworth" → specific file lookup

---

## Phase 4: Test Case - iCalendar for Mozworth

### 4.1 Test Scenario
```
User: "Let's create a new icalendar for mozworth and add Oct event that is the release of Why Not"
```

### 4.2 Expected Agent Behavior
1. **Search**: `file_lookup("mozworth", ".ics")` → find existing iCal files
2. **If found**: 
   - `file_read(file_id)` → get current .ics content
   - Parse iCal, add new VEVENT (Oct, "Why Not Release")
   - `file_update(file_id, new_ics_content)`
3. **If not found**:
   - Generate new iCal with VEVENT
   - `file_write("mozworth_calendar.ics", ics_content, ".ics")`
4. **Response**: Confirm creation/update, show event details

### 4.3 Test Implementation
- Unit test: `test_file_lookup_ical_matches()`
- Integration test: `test_agent_creates_ical_for_mozworth()`
- End-to-end: Full chat flow with file creation

---

## Phase 5: UI Components

### 5.1 Chat Input File Selector
```html
<!-- In chat.html input toolbar -->
<div class="input-toolbar">
  <button id="file-attach-btn" class="tool-btn" title="Attach file">
    <svg>📎</svg>
  </button>
  <input type="file" id="file-input" multiple accept=".txt,.csv,.json,.xml,.html,.ics" style="display:none">
  <div id="attached-files-preview" class="file-chips"></div>
</div>
```

### 5.2 File Chips Component
```html
<div class="file-chip" data-file-id="...">
  <span class="file-icon">📄</span>
  <span class="file-name">document.txt</span>
  <button class="remove-btn">×</button>
</div>
```

### 5.3 Files Tab Navigation
```html
<!-- In chat.html header -->
<nav class="main-nav">
  <a href="/chat-ui" class="nav-item active">Chat</a>
  <a href="/files-ui" class="nav-item">Files</a>
  <a href="/brain-ui" class="nav-item">Brain</a>
</nav>
```

---

## API Endpoints Needed

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/files/upload` | POST | Upload files (exists) |
| `/files/list` | GET | List all user files |
| `/files/{id}` | GET | Get file details/content |
| `/files/{id}/content` | GET | Download file |
| `/files/{id}` | DELETE | Delete file |
| `/files/search` | GET | Search files by name/content |

---

## Implementation Order

1. **Backend**: Add file tools (`file_lookup`, `file_read`, `file_write`, `file_update`)
2. **Backend**: Add file search/list endpoints
3. **Frontend**: Add file selector to chat input
4. **Frontend**: Create `files.html` with list/detail views
5. **Integration**: Wire file tools into agent
6. **Test**: iCalendar Mozworth test case

---

## Design Principles Alignment

- **Model-first**: Agent decides file operations via tools
- **No templated responses**: Model generates file content
- **Lean on model**: Agent decides file operations via reasoning
- **Scheduled tasks are memory**: Files are first-class frames
- **Clean ship**: No dead code, proper imports
- **Stability**: Regression test for iCal creation/update
