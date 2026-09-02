# File UI Integration Plan - Fact-Checked & Enriched

## Overview
Wire up file input/output capabilities to the chat UI and add a dedicated Files management tab.
**Status**: Based on `file_input_output_plan.md` requirements and current implementation status.

---

## Implementation Status Check

| Phase | Original Plan | Current Status | Gap |
|-------|---------------|----------------|-----|
| **Phase 1: Core File Support** | File upload, parsing, local storage | ✅ **Complete** | `POST /files/upload` exists, `files.py` extraction works |
| **Phase 2: Memory Integration** | File content → slots/associations, source tracking | ✅ **Complete** | Frames/slots created with reliability 0.7 |
| **Phase 3: Advanced Features** | File generation, NL querying, export | ✅ **Partial** | `generate_file()`, `query_content()` exist but not wired to agent |

---

## Fact-Checked Requirements from `file_input_output_plan.md`

### ✅ Already Implemented (Phase 1-3 Backend)
| Feature | Status | Location |
|---------|--------|----------|
| File upload endpoint (`POST /files/upload`) | ✅ Done | `main.py` |
| File type validation (.txt, .csv, .json, .xml, .html) | ✅ Done | `main.py` + `files.py` |
| 10MB size limit | ✅ Done | `main.py` |
| Content extraction (text, entities, questions) | ✅ Done | `files.py` |
| Memory integration (frames/slots, reliability 0.7) | ✅ Done | `main.py` upload endpoint |
| File generation (`generate_file`) | ✅ Done | `files.py` |
| Natural language query (`query_content`) | ✅ Done | `files.py` |

### ❌ Not Yet Implemented (UI & Agent Integration)
| Feature | Required By | Status |
|---------|-------------|--------|
| File upload in chat UI (file selector button) | Phase 1 UI | ❌ Missing |
| Drag-and-drop file upload | Phase 1 UI | ❌ Missing |
| Files tab in UI (`/files-ui`) | Phase 2 UI | ❌ Missing |
| File list/detail views | Phase 2 UI | ❌ Missing |
| Agent file tools (`file_lookup`, `file_read`, `file_write`, `file_update`) | Phase 3 Backend | ❌ Missing |
| Agent tool registration in `builtin_tools()` | Phase 3 | ❌ Missing |
| File search/list endpoints (`/files/list`, `/files/search`) | Phase 2 UI | ❌ Missing |
| Download/delete endpoints | Phase 2 UI | ❌ Missing |
| iCalendar test case | Requirements | ❌ Missing |

---

## Enriched Requirements

### 1. Privacy & Security (from original plan)
- ✅ All processing local (Ollama)
- ✅ No external APIs
- ✅ File handling in user's local storage (`/app/data`)
- ⚠️ Need: Access controls, sandboxing, cleanup procedures for old files

### 2. File Type Support Matrix
| Type | Input | Output | Preview | Query |
|------|-------|--------|---------|-------|
| `.txt` | ✅ | ✅ | ✅ | ✅ |
| `.csv` | ✅ | ❌ | ❌ | ✅ |
| `.json` | ✅ | ❌ | ❌ | ✅ |
| `.xml` | ✅ | ❌ | ❌ | ✅ |
| `.html` | ✅ | ❌ | ❌ | ✅ |
| `.ics` | ⚠️ (upload only) | ❌ | ❌ | ⚠️ |

### 3. Missing File Type: iCalendar (.ics)
The test case requires `.ics` support. Current extractors handle `.txt`, `.csv`, `.json`, `.xml`, `.html`. Need to add `.ics` extractor.

### 4. Agent Tool Requirements
The original plan says: "Extend `fetch_url` concept to local file processing. Add new file processing tools that integrate with existing pipeline."

**Required Tools:**
```python
# In pipeline/tools.py
file_lookup(name_pattern: str, file_type: str = None) -> list[FileInfo]
file_read(file_id: int) -> FileContent  
file_write(name: str, content: str, file_type: str) -> FileFrame
file_update(file_id: int, new_content: str) -> FileFrame
file_delete(file_id: int) -> bool
file_search(query: str, file_type: str = None) -> list[FileInfo]
```

### 5. iCalendar Test Requirements (from user)
User requirement: "Let's create a new icalendar for mozworth and add Oct event that is the release of Why Not"

**Required Implementation:**
1. Agent receives natural language request
2. Agent uses `file_lookup("mozworth", ".ics")` to find existing .ics
3. If found: `file_read()` → parse iCal → add VEVENT → `file_update()`
4. If not found: generate iCal with VEVENT → `file_write()`
5. Verify: iCal file exists with correct VEVENT

---

## Revised Implementation Plan

### Priority 1: Backend Agent Tools (Week 1)
1. Add file tools to `pipeline/tools.py`
2. Register in `builtin_tools()`
3. Add file search/list endpoints (`/files/list`, `/files/search`, `/files/{id}`)

### Priority 2: iCalendar Support (Week 1-2)
1. Add `.ics` extractor to `files.py`
2. Add iCal generation helper
3. Implement iCal test case

### Priority 3: UI Integration (Week 2-3)
1. Add file selector to `chat.html`
2. Create `files.html` with list/detail views
3. Add Files tab to navigation

### Priority 4: Integration & Testing (Week 2)
1. Wire file tools into agent
2. End-to-end iCalendar test
3. E2E test: upload → query → generate

---

## Design Principle Verification

| Principle | Status |
|-----------|--------|
| Model-first correction | Agent uses tools, not scripted logic |
| No templated responses | Model generates file content |
| Lean on model flexibility | Agent decides file ops via tools |
| Scheduled tasks are memory | Files = frames/slots |
| Clean ship | Lint clean, no dead code |
| Stability | Need iCal regression test |

---

## Next Steps (Priority Order)

1. **Add file tools to `pipeline/tools.py`** - Core agent integration
2. **Add file search/list endpoints** - Enable UI listing
3. **Add .ics extractor** - Required for iCal test
4. **Implement iCal generation** - For test case
5. **Add iCalendar test** - Prove it works
5. **Wire tools into agent** - Register in `builtin_tools()`
6. **UI: File selector in chat.html** - Phase 1 UI
6. **UI: Files tab (files.html)** - Phase 2 UI
