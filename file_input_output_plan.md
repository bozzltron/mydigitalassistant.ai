# File Input/Output Support - Master Plan (Fact-Checked)

## Overview
Implement file-based input/output capabilities for MyDigitalAssistant, enabling users to upload text-based files for processing and generate new files as outputs. This enhancement integrates with the existing memory system while maintaining the project's privacy-first architecture.

All processing occurs locally via the Ollama instance at `localhost:11434`. No external APIs or cloud services are involved.

**Status Legend**: ✅ Implemented | 🟡 Partial | ❌ Not Started | ⚠️ Inaccurate in previous version

---

## 1. Supported File Types

### Input Formats
| Format | Status | Notes |
|--------|--------|-------|
| **.txt** | ✅ | Plain text documents |
| **.csv** | ✅ | Comma-separated values |
| **.json** | ✅ | JavaScript Object Notation |
| **.xml** | ✅ | Extensible Markup Language |
| **.html, .htm** | ✅ | HTML documents |
| **.ics** | ❌ | iCalendar - **NOT IMPLEMENTED** (in accept attr but not in allowed_types, no extractor) |

### Output Formats
| Format | Status | Notes |
|--------|--------|-------|
| Generated `.csv`, `.json`, `.txt` | ❌ | Via `file_write` tool - tool exists but no API endpoint |
| Enhanced uploaded documents | ❌ | Via `file_update` tool - tool exists but no API endpoint |
| New iCalendar files | ❌ | Not implemented |

---

## 2. Privacy Requirements
✅ **All met by design:**
- Locally only using Ollama at `localhost:11434`
- Never transfer data to external services or cloud APIs
- Secure storage in `/app/data/`
- No tracking or logging of file contents

---

## 3. Core System Integration

### Memory System Integration
| Feature | Status | Notes |
|---------|--------|-------|
| Extract file content into slots/associations | ✅ | In `/files/upload` endpoint |
| Store source metadata with `source_type="file"` | ✅ | Reliability 0.7 |
| Cross-referencing file content with memory | ✅ | Frames have embeddings |
| Files as first-class memory frames | ✅ | Created in `/files/upload` |

### Tool System Integration
| Feature | Status | Notes |
|---------|--------|-------|
| File tools registered in `builtin_tools()` | ✅ | 6 tools: lookup, read, write, update, delete, search |
| Native Ollama tool-calling | ✅ | All tools return string results |
| Extend `fetch_url` concept to local files | ✅ | Similar pattern with auto-extraction |

### API Endpoints

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/files/upload` | POST | ✅ **Implemented** | Upload + extract + store in memory |
| `/files/list` | GET | ❌ **Missing** | List all user files |
| `/files/{frame_id}` | GET | ❌ **Missing** | Get file details and content |
| `/files/{frame_id}/content` | GET | ❌ **Missing** | Download file as text |
| `/files/{frame_id}` | DELETE | ❌ **Missing** | Delete file from memory and disk |
| `/files/search` | GET | ❌ **Missing** | Search file content for query |
| `/files/lookup` | POST | ❌ **Missing** | Tool-only, no REST endpoint |
| `/files/read` | POST | ❌ **Missing** | Tool-only, no REST endpoint |
| `/files/write` | POST | ❌ **Missing** | Tool-only, no REST endpoint |
| `/files/update` | POST | ❌ **Missing** | Tool-only, no REST endpoint |
| `/files/delete` | POST | ❌ **Missing** | Tool-only, no REST endpoint |
| `/chat` | POST | 🟡 **Partial** | Accepts `attached_files` but UI doesn't send properly |

### Data Flow (Actual)
```
1. User uploads file via UI → API /files/upload stores locally ✅
2. System parses file content → Extracts meaningful text/structure ✅
3. Content stored in agent memory (frames/slots with embeddings) ✅
4. User queries reference file data via agent tools ✅ (tools exist)
5. Output generation creates new files via file_write tool 🟡 (tool exists, no API)
6. Generated files available for download ❌ (no download endpoint)
```

---

## 4. UI/UX Requirements

### File Upload Interface
| Feature | Status | Notes |
|---------|--------|-------|
| Dedicated "Files" tab (`files.html`) | ❌ **Missing** | No route, no file |
| Drag-and-drop in chat input | ✅ | Works on input-row |
| File type validation | ✅ | In `/files/upload` |
| Size limit (10MB) | ✅ | In `/files/upload` |
| Visual feedback during upload | ❌ | No progress indicator |
| File browser with previews | ❌ | No Files tab |

### File Management
| Feature | Status | Notes |
|---------|--------|-------|
| Local directory `/app/data/` | ✅ | Created on upload |
| Timestamp-based naming | ✅ | `upload_YYYYMMDD_HHMMSS.file` |
| Cleanup procedures | ❌ | No GC for old files |

### Input Row Integration (chat.html)

| Feature | Status | Actual State |
|---------|--------|--------------|
| **Button consistency** | ❌ **NOT DONE** | file-attach-btn has custom CSS (lines 510-525); mic-btn has custom CSS; send-btn has NO btn class |
| **Horizontal file chips** | ❌ **NOT DONE** | file-chips INSIDE .file-attach div → renders vertically |
| **Send with files** | ❌ **NOT DONE** | Custom `sendForm()` still used (line 2870); doesn't call `sendMessage()` |
| **Drag-and-drop** | ✅ | Works on input-row |

### Files Tab (`/files-ui`)
| Feature | Status |
|---------|--------|
| File list view | ❌ Missing |
| File detail view | ❌ Missing |
| Actions: View, Copy, Download, Delete | ❌ Missing |
| Bulk actions | ❌ Missing |

### Search & Filter
| Feature | Status |
|---------|--------|
| Filter by type/date/source | ❌ Missing |
| Search by filename/content | 🟡 Tool exists (`file_search`), no UI |

---

## 5. Processing Pipeline

### Input Processing
| Format | Extractor Status | Notes |
|--------|------------------|-------|
| .txt | ✅ | `extract_text_from_txt` |
| .csv | ✅ | `extract_text_from_csv` |
| .json | ✅ | `extract_text_from_json` |
| .xml | ✅ | `extract_text_from_xml` |
| .html | ✅ | `extract_text_from_html` |
| .ics | ❌ | **NO EXTRACTOR** - only in accept attr |

### Output Generation
| Feature | Status |
|---------|--------|
| Data transformation via LLM | 🟡 Tool `file_write` exists |
| Format conversion | ❌ Not implemented |
| Natural language queries | 🟡 Tools exist, no UI integration |
| iCalendar generation | ❌ Not implemented |

### LLM Integration
| Feature | Status |
|---------|--------|
| Agent uses file tools | ✅ Tools registered in `builtin_tools()` |
| Model decides file operations | ✅ Native Ollama tool-calling |
| No scripted logic | ✅ Tools called by model |

---

## 6. Technical Implementation Details

### Backend Components
| Component | Status | Location |
|-----------|--------|----------|
| File handler module | ✅ | `assistant/backend/pipeline/files.py` |
| Parser library | ✅ | txt, csv, json, xml, html (no ics) |
| Memory adapter | ✅ | Uses existing extraction system |
| Security middleware | ❌ | No dedicated middleware |

### File Tools (in `pipeline/tools.py`) - ALL ✅ IMPLEMENTED
| Tool | Handler | Description |
|------|---------|-------------|
| `file_lookup` | `_handle_file_lookup` | Search frames by name pattern + optional type |
| `file_read` | `_handle_file_read` | Read content by frame ID |
| `file_write` | `_handle_file_write` | Create new file + frame |
| `file_update` | `_handle_file_update` | Update existing file content |
| `file_delete` | `_handle_file_delete` | Delete frame + file |
| `file_search` | `_handle_file_search` | Search content for query term |

### Configuration (`.env`)
```bash
# File support - NOT YET CONFIGURABLE (hardcoded)
# MAX_FILE_SIZE=10485760  # 10MB - hardcoded in main.py:1482
# SUPPORTED_EXTENSIONS=txt,csv,json,xml,html,ics - hardcoded in main.py:1472
```

---

## 7. Security Considerations

### Access Controls
| Feature | Status | Notes |
|---------|--------|-------|
| Restricted to `/app/data/` | ✅ | Upload saves there |
| No external FS access | ✅ | Paths resolved relative |
| Read-only in memory | ✅ | Slots are read for retrieval |
| Secure deletion | ❌ | No GC/cleanup implemented |

### Data Protection
| Feature | Status |
|---------|--------|
| Local-only processing | ✅ |
| No metadata beyond session | 🟡 Frames persist for retrieval |
| Encryption | ❌ Not implemented |
| Privacy compliance | ✅ By design |

### Sandboxing
| Feature | Status |
|---------|--------|
| Paths relative to `/app/data/` | ✅ |
| No parent directory access | ✅ |
| Timestamp-safe filenames | ✅ |

---

## 8. Performance Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Max file size 10MB | ✅ | Enforced in upload |
| Timeout 30s | ❌ | Not enforced for processing |
| Memory optimization | 🟡 | Basic parsing, no streaming |
| Concurrent uploads | ❌ | Not tested |

---

## 9. Testing Requirements

| Test Category | Status | Notes |
|---------------|--------|-------|
| Unit: file type validation | ❌ | No tests for file tools |
| Unit: parsing each format | ❌ | No tests for extractors |
| Unit: memory integration | ❌ | No tests for file→slots |
| Unit: security boundaries | ❌ | No tests |
| Integration: upload→process→download | ❌ | No download endpoint |
| Integration: memory verification | ❌ | No tests |
| Integration: privacy compliance | ❌ | No tests |
| UAT: UI navigation | ❌ | No Files tab |
| UAT: real-world scenarios | ❌ | Not tested |
| UAT: cross-format conversion | ❌ | Not implemented |

### Critical Path Tests (per AGENTS.md)
| Test | Status |
|------|--------|
| Learn-a-fact-then-recall | ❌ Not tested for files |
| Contradiction-then-auto-resolve | ❌ Not tested for files |
| File upload then recall | ❌ Not tested |

---

## 10. Future Extensibility

| Feature | Status |
|---------|--------|
| .pdf, .docx, .xlsx support | ❌ Future |
| Custom format handlers | ❌ Future |
| Plugin architecture | ❌ Future |
| Batch processing | ❌ Future |
| Scheduled automation | ❌ Future |
| Collaborative sharing | ❌ Future |
| Version control tracking | ❌ Future |

---

## 11. Implementation Phases (Corrected)

### Phase 1: Core File Support ✅ **COMPLETED**
- File upload endpoint (`POST /files/upload`)
- Basic file parsing for .txt, .csv, .json, .xml, .html
- Local storage in `/app/data/`
- Memory integration (frames/slots with reliability 0.7)

### Phase 2: Agent Tools ✅ **COMPLETED** (tools only)
- File tools in `pipeline/tools.py`: 6 tools registered in `builtin_tools()`
- ⚠️ **NO API endpoints** for these tools (list, search, read, write, update, delete)
- ⚠️ **NO .ics extractor** in files.py
- ⚠️ **NO .ics in allowed_types** for upload

### Phase 3: UI Integration ❌ **PENDING** (NOT DONE)
- [ ] Fix button consistency (use `.btn-icon` for all 3 buttons)
- [ ] Horizontal file chips (move outside `.file-attach`)
- [ ] Fix send with files (use `sendMessage()` with `attached_files`)
- [ ] Create `/files-ui` route + `files.html`
- [ ] File list/detail views with actions

### Phase 4: API Endpoints ❌ **PENDING**
- [ ] `GET /files/list`
- [ ] `GET /files/search`
- [ ] `GET /files/{frame_id}`
- [ ] `GET /files/{frame_id}/content`
- [ ] `DELETE /files/{frame_id}`

### Phase 5: iCalendar Support ❌ **PENDING**
- [ ] Add `.ics` to allowed_types
- [ ] Add `extract_text_from_ics` to files.py
- [ ] Add iCal generation helper

### Phase 6: Integration & Testing ❌ **PENDING**
- [ ] Wire file tools into agent reasoning flow (verify they're called)
- [ ] End-to-end iCalendar Mozworth test case
- [ ] E2E test: upload → query → generate
- [ ] Regression tests per critical path

---

## 12. Success Metrics (Updated)

| Metric | Target | Current |
|--------|--------|---------|
| Supported file types | 6 (txt, csv, json, xml, html, ics) | 5 (no ics) |
| Privacy: no external transfer | 100% | ✅ |
| Processing time <30s | Typical files | Unknown |
| UI usability | Intuitive with feedback | ❌ Broken |
| File operations success rate | 99%+ | Unknown |
| Agent can use files via NL | Yes | 🟡 Tools exist, no API |

---

## 13. Design Principle Alignment (Verified)

| Principle | Verified |
|-----------|----------|
| **Model-first correction** | ✅ Agent decides via tools |
| **No templated responses** | ✅ Model generates content |
| **Lean on model flexibility** | ✅ Tools not scripted |
| **Scheduled tasks are memory** | ✅ Files as frames/slots |
| **Clean ship** | 🟡 Ruff passes, but dead code in chat.html (custom sendForm, custom btn styles) |
| **Stability: no regressions** | ❌ No regression tests for file features |

---

## 14. Files Modified/Created (Accurate)

### Modified (in commits e7a789f, f7822f2)
- `assistant/backend/pipeline/tools.py` - Added 6 file tools + datetime, calculate, fetch_url, web_search
- `assistant/backend/main.py` - /chat endpoint processes attached_files; /files/upload endpoint
- `assistant/backend/pipeline/orchestrator.py` - ChatRequest has attached_files field
- `assistant/backend/pipeline/files.py` - **NO .ics extractor added**
- `assistant/backend/static/chat.html` - **NO UI fixes applied yet**
- `assistant/AGENTS.md` - Added "Clean Code & Modularity" section

### Created
- `TOOLS_DOCUMENTATION.md` - Comprehensive tool docs
- `FILE_SUPPORT_ANALYSIS_AND_PLAN.md` - Analysis + plan
- `FILE_UI_INTEGRATION_PLAN_v2.md` - Fact-checked UI plan

### Deleted
- `plans/HANDOFF.md` - Old plan
- `file_input_output_requirements.md` - Superseded
- `FILE_UI_INTEGRATION_PLAN.md` - Superseded by v2

---

## 15. Quick Reference: Actual Tool Commands

### Via Agent Tools (native Ollama tool-calling)
These work when the agent calls them during chat:

```python
# Agent calls these internally - no REST API
file_lookup("mozworth", "ics")    # Search by name pattern
file_read(42)                      # Read by frame ID
file_write("calendar", content, "ics")  # Create file
file_update(42, new_content)       # Update file
file_delete(42)                    # Delete file
file_search("query", "csv")        # Search content
```

### Via REST API (only upload works)
```bash
# Upload a file - WORKS
curl -X POST http://127.0.0.1:8080/files/upload -F "file=@document.txt"

# List files - DOESN'T EXIST
curl http://127.0.0.1:8080/files/list?user_id=1

# Search files - DOESN'T EXIST
curl "http://127.0.0.1:8080/files/search?query=mozworth&user_id=1"
```

---

## 16. Agent Natural Language Examples (Tool Sequences)

| User Prompt | Expected Agent Tool Sequence |
|-------------|------------------------------|
| "Create icalendar for mozworth with Oct event: Why Not release" | `file_lookup("mozworth", "ics")` → if found: `file_read()` → parse → add VEVENT → `file_update()`; else: generate iCal → `file_write()` |
| "Read the CSV we uploaded last week" | `file_search("mozworth", "csv")` → `file_read(file_id)` |
| "What's in the JSON file about mozworth?" | `file_search("mozworth", "json")` → `file_read(file_id)` |
| "Summarize data in my CSV file" | `file_lookup("csv", "csv")` → `file_read()` → LLM processes → `file_write("summary", content, "txt")` |
| "Delete the file I uploaded earlier" | `file_search("*.txt", context)` → identify file_id → `file_delete(file_id)` |

---

## 17. Immediate Next Steps (Priority Order)

### Priority 1: Fix Chat Input UI (1-2 hours)
1. **Button consistency**: Update chat.html to use `.btn-icon` for file-attach, mic, and `.btn-primary` for send
2. **Horizontal file chips**: Move `#file-chips-preview` outside `.file-attach`, into `#input-row` flex container
3. **Fix send with files**: Replace custom `sendForm()` with `sendMessage()` + `attached_files` in body

### Priority 2: Add Missing API Endpoints (2-4 hours)
4. `GET /files/list` - return all file frames for user
5. `GET /files/search` - search by query + optional type
6. `GET /files/{frame_id}` - return file details + content
7. `GET /files/{frame_id}/content` - download as text
8. `DELETE /files/{frame_id}` - delete frame + file

### Priority 3: iCalendar Support (2-4 hours)
9. Add `.ics` to `allowed_types` in main.py
10. Add `extract_text_from_ics()` to files.py
11. Add iCal generation helper for `file_write`

### Priority 4: Files Tab UI (4-8 hours)
12. Create `/files-ui` route in main.py
13. Create `files.html` with list/detail views
14. Add "Files" nav item in header

### Priority 5: Testing & Integration (4-8 hours)
15. Unit tests for file tools + extractors
16. Integration test: upload → query via agent → verify response
17. iCalendar Mozworth E2E test
18. Regression tests per AGENTS.md critical paths

---

## 18. Known Issues to Fix

| Issue | Location | Fix |
|-------|----------|-----|
| Custom CSS overrides `.btn` | chat.html:510-525 | Remove, use `.btn-icon` |
| file-chips vertical | chat.html:1090 | Move to input-row flex |
| Custom sendForm | chat.html:2870 | Remove, use sendMessage |
| No /files/list endpoint | main.py | Add endpoint |
| No /files/search endpoint | main.py | Add endpoint |
| No .ics extractor | files.py | Add extract_text_from_ics |
| .ics not in allowed_types | main.py:1472 | Add "ics" |
| No cleanup/GC | - | Add to scheduler |

---

This plan now accurately reflects the current implementation state. Use this as the source of truth for handoff to the next agent.