# File Input/Output Support - Master Plan

## Overview
Implement file-based input/output capabilities for MyDigitalAssistant, enabling users to upload text-based files (TXT, CSV, JSON, XML, HTML, iCalendar) for processing and generate new files as outputs. This enhancement integrates with the existing memory system while maintaining the project's privacy-first architecture.

All processing occurs locally via the Ollama instance at `localhost:11434`. No external APIs or cloud services are involved.

---

## 1. Supported File Types

### Input Formats
- **.txt** - Plain text documents
- **.csv** - Comma-separated values for tabular data
- **.json** - JavaScript Object Notation for structured data
- **.xml** - Extensible Markup Language for hierarchical data
- **.html, .htm** - HTML documents for web scraping or analysis
- **.ics** - iCalendar files for scheduling/events (Phase 2+)

### Output Formats
- Generated `.csv`, `.json`, `.txt` files from processing results
- Enhanced versions of uploaded documents with modifications
- New iCalendar files with VEVENT/VTODO components

---

## 2. Privacy Requirements

All file processing must occur:
- **Locally only** using the local Ollama instance at `localhost:11434`
- **Never** transfer data to external services or cloud APIs
- **Secure storage** within user's local file system directory (`/app/data/`)
- **Respect user privacy** with no tracking or logging of file contents

---

## 3. Core System Integration

### Memory System Integration
- Extract file content into existing memory slots/associations
- Store source metadata with `source_type="file"` and reliability score (0.7)
- Enable cross-referencing between file content and other memory items
- Files are first-class memory frames with embeddings for retrieval

### Tool System Integration
- Extend `fetch_url` concept to local file processing
- Add new file processing tools that integrate seamlessly with existing pipeline
- Maintain consistency with document extraction methods used for URLs
- Agent tools registered in `builtin_tools()`: `file_lookup`, `file_read`, `file_write`, `file_update`, `file_delete`, `file_search`

### API Endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/files/upload` | POST | Upload and process new files |
| `/files/list` | GET | List all user files (frame IDs, names, types, sizes) |
| `/files/{frame_id}` | GET | Get file details and content |
| `/files/{frame_id}/content` | GET | Download file as text |
| `/files/{frame_id}` | DELETE | Delete file from memory and data directory |
| `/files/search` | GET | Search file content for query term |
| `/chat` | POST | Send message with attached files |

### Data Flow
```
1. User uploads file via UI → API /files/upload stores locally
2. System parses file content → Extracts meaningful text/structure
3. Content stored in agent memory (frames/slots with embeddings)
4. User queries or commands can reference file data via agent tools
5. Output generation creates new files from processed results via file_write
6. Generated files stored and available for download via /files/{id}/content
```

---

## 4. UI/UX Requirements

### File Upload Interface
- **Dedicated "Files" tab** in main application interface (new `files.html` route)
- **Drag-and-drop file upload** functionality in chat input
- **File type validation** and size limit enforcement (max 10MB)
- **Visual feedback** during upload progress
- **File browser component** with previews where appropriate

### File Management
- **Local file system directory** for user files (`/app/data/`)
- **Upload/download capabilities** with proper access controls
- **File naming conventions**: `upload_YYYYMMDD_HHMMSS.file` (timestamp-based, secure)
- **Cleanup procedures** for old temporary files (GC during consolidation)

### Input Row Integration (chat.html)
- **File attach button** (`📎`) consistent padding/sizing with send/mic buttons
- **File chips preview** horizontally next to attach button (not vertical stack)
- **Send with files**: User message + file chips appear in chat thread
- **Drag-and-drop** onto chat input area with visual feedback

### Files Tab (`/files-ui`)
- **File list view**: Grid of all user files with type icon, name, size, upload date, source badge
- **File detail view**: Text/code syntax highlighting, JSON tree, CSV table, iCal formatted events
- **Actions per file**: View content, Copy, Download, Delete
- **Bulk actions**: Select multiple, delete, download zip

### Search & Filter
- Filter by file type, date range, source (uploaded/generated/extracted)
- Search by filename or content using `file_search` tool

---

## 5. Processing Pipeline Requirements

### Input Processing
1. **Validation**: Verify supported file types against whitelist
2. **Content Extraction**: Parse files to extract meaningful text:
   - CSV: Convert to structured format, preserve headers
   - JSON/XML/HTML: Extract text content while maintaining context
   - TXT: Direct processing with formatting preservation
   - iCal: Parse VEVENT components, extract events

### Output Generation
1. **Data Transformation**: Support LLMs to modify or generate new file contents
2. **Format Conversion**: Allow conversion between supported formats
3. **Natural Language Queries**: Enable processing of structured data through natural language prompts
4. **iCalendar generation**: Create VEVENT/VTODO components from natural language requests

### LLM Integration
- Modify memory modules to handle document-based queries
- Enable LLMs to reference and modify file contents
- Support natural language queries against structured data
- Allow generation of new documents/tables from analysis
- Agent uses tools (file_read/file_write/file_update) rather than scripted logic

---

## 6. Technical Implementation Details

### Backend Components
- **File handler module**: `assistant/backend/pipeline/files.py` - extraction for .txt, .csv, .json, .xml, .html, .ics
- **Parser library**: Support for CSV, JSON, XML, HTML, iCal parsing
- **Memory adapter**: Integration with existing document extraction system
- **Security middleware**: Validate all file operations against privacy policy

### File Tools (in `pipeline/tools.py`)
All tools are native Ollama tool-calling, local-only:

| Tool | When to Use |
|------|-------------|
| `file_lookup` | User refers to "the file called X" or "the ical file" |
| `file_read` | After file_lookup to get file content |
| `file_write` | User says "create a new file called X" or "write Y to file Z" |
| `file_update` | After file_lookup to update existing file content |
| `file_delete` | User wants to remove a file |
| `file_search` | User wants to find specific information inside their files |

### Configuration (`.env`)
```bash
# File support
MAX_FILE_SIZE=10485760  # 10MB
SUPPORTED_EXTENSIONS=txt,csv,json,xml,html,ics

# Search backend
SEARCH_BASE_URL=http://127.0.0.1:8080
SEARCH_MIN_RELEVANCE=0.30  # for SearXNG

# Brave Search (optional)
BRAVE_ENABLED=false
BRAVE_API_KEY=
```

---

## 7. Security Considerations

### Access Controls
- File operations restricted to user's designated local directory (`/app/data/`)
- No external file system access permitted
- Read-only operations for file content in memory
- Secure deletion of temporary processing files

### Data Protection
- All file contents processed locally only
- No metadata or content stored beyond current session (except frames/slots for retrieval)
- Encryption for sensitive file data where appropriate
- Compliance with existing privacy policies

### Sandboxing
- All file paths resolved relative to `/app/data/`
- No access to parent directories or system files
- Timestamp-based safe filenames prevent path traversal

---

## 8. Performance Requirements

### Processing Limits
- **Maximum file size**: 10MB per upload
- **Timeout limits**: 30 seconds for processing large files
- **Memory usage**: Optimized parsing to prevent memory leaks
- **Concurrent operations**: Support for multiple simultaneous file uploads

### User Experience
- **Real-time upload progress indicators**
- **Instant response** to query commands on loaded files
- **Visual feedback** for processing completion
- **Error handling** with clear user messages

---

## 9. Testing Requirements

### Unit Tests
- File type validation checks
- Parsing functionality for each supported format (txt, csv, json, xml, html, ics)
- Memory storage integration tests
- Security boundary enforcement

### Integration Tests
- End-to-end file upload → processing → download workflow
- Memory integration verification
- Privacy compliance checks
- Performance benchmarks

### User Acceptance Tests
- UI navigation and functionality testing
- Real-world file processing scenarios
- Cross-format conversion validation

### Critical Path Tests (per AGENTS.md)
1. **Learn-a-fact-then-recall**: Upload fact, query it back
2. **Contradiction-then-auto-resolve**: Upload conflicting data, verify auto-resolution
3. **File upload then recall**: Upload file, ask assistant about it, verify response

---

## 10. Future Extensibility

### Additional File Types
- Support for additional formats (.pdf, .docx, .xlsx) in future releases
- Custom format handlers for specific user needs
- Plugin architecture for third-party file type support

### Advanced Features
- **Batch processing** of multiple files
- **Scheduled automation workflows** (daily/weekly file processing)
- **Collaborative file sharing** capabilities (per-user isolation)
- **Version control tracking** for file modifications

---

## 11. Implementation Phases

### Phase 1: Core File Support (Completed)
- File upload endpoint (`POST /files/upload`)
- Basic file parsing for supported formats (.txt, .csv, .json, .xml, .html)
- Local storage integration in `/app/data/`
- Memory integration (frames/slots with reliability 0.7)

### Phase 2: Agent Tools (Completed in this commit)
- File tools in `pipeline/tools.py`: `file_lookup`, `file_read`, `file_write`, `file_update`, `file_delete`, `file_search`
- Registration in `builtin_tools()`
- API endpoints: `/files/list`, `/files/search`, `/files/{id}`, `/files/{id}/content`, `/files/{id}`
- .ics extractor added to `files.py`

### Phase 3: UI Integration (Pending)
- File selector in chat input (horizontal chips, consistent buttons)
- Dedicated "Files" tab (`/files-ui` route + `files.html`)
- File list/detail views with actions

### Phase 4: Integration & Testing (Pending)
- Wire file tools into agent reasoning flow
- End-to-end iCalendar Mozworth test case
- E2E test: upload → query → generate
- Regression tests per critical path priority

---

## 12. Success Metrics

1. **Functionality**: All supported file types process correctly (txt, csv, json, xml, html, ics)
2. **Privacy**: No external data transfer in any workflow (verified by design)
3. **Performance**: Processing time under 30 seconds for typical files (<1MB)
4. **Usability**: Intuitive UI with clear feedback during operations
5. **Reliability**: 99%+ success rate for file operations
6. **Agent usability**: Agent can lookup/read/write files via natural language prompts

---

## 13. Design Principle Alignment

| Principle | Application |
|-----------|-------------|
| **Model-first correction** | Agent decides file operations via tools, not scripted logic |
| **No templated responses** | Model generates file content (iCal, CSV, JSON) |
| **Lean on model flexibility** | Agent reasons about which file tool to use |
| **Scheduled tasks are memory** | Files stored as frames/slots with embeddings |
| **Clean ship** | No dead code, shared components, lint clean (ruff passes) |
| **Stability: no regressions** | Critical path tests pass; bounded tool loops |

---

## 14. Files Modified/Created

### Modified
- `assistant/backend/pipeline/tools.py` - Added 6 file tools + datetime, calculate, fetch_url, web_search
- `assistant/backend/main.py` - /chat endpoint processes attached_files
- `assistant/backend/pipeline/orchestrator.py` - ChatRequest has attached_files field
- `assistant/backend/pipeline/files.py` - Added .ics extractor
- `assistant/backend/static/chat.html` - Button consistency, horizontal file chips, send with files
- `assistant/AGENTS.md` - Added "Clean Code & Modularity" section

### Created
- `TOOLS_DOCUMENTATION.md` - Comprehensive tool docs
- `FILE_SUPPORT_ANALYSIS_AND_PLAN.md` - Analysis + plan (supersedes old redundant docs)
- `FILE_UI_INTEGRATION_PLAN.md` - UI plan (fact-checked v2)
- `FILE_UI_INTEGRATION_PLAN_v2.md` - Fact-checked & enriched version

### Deleted
- `plans/HANDOFF.md` - Old plan, removed per clean ship principle

---

## 15. Quick Reference: Tool Commands

### Upload a file
```bash
curl -X POST http://127.0.0.1:8080/files/upload -F "file=@document.txt"
```

### List user files
```bash
curl http://127.0.0.1:8080/files/list?user_id=1
```

### Search files
```bash
curl "http://127.0.0.1:8080/files/search?query=mozworth&user_id=1"
```

### Read a file (after lookup)
```bash
# First lookup
curl "http://127.0.0.1:8080/files/lookup?name_pattern=mozworth&user_id=1"

# Then read
curl http://127.0.0.1:8080/files/read?file_id=42
```

### Create a new file
```bash
curl -X POST http://127.0.0.1:8080/files/write -d '{"name": "calendar", "content": "BEGIN:VCALENDAR...", "file_type": "ics"}' -H "Content-Type: application/json"
```

### Update a file
```bash
curl -X POST http://127.0.0.1:8080/files/update -d '{"file_id": 42, "new_content": "BEGIN:VCALENDAR...UPDATED"}' -H "Content-Type: application/json"
```

### Delete a file
```bash
curl -X DELETE http://127.0.0.1:8080/files/delete?file_id=42
```

---

## 16. Agent Natural Language Examples

| User Prompt | Agent Tool Sequence |
|-------------|---------------------|
| "Let's create a new icalendar for mozworth and add Oct event that is the release of Why Not" | `file_lookup("mozworth", "ics")` → if found: `file_read()` → parse → add VEVENT → `file_update()`; if not found: generate iCal → `file_write()` |
| "Read the CSV we uploaded last week" | `file_search("mozworth", "csv")` → `file_read(file_id)` |
| "What's in the JSON file about mozworth?" | `file_search("mozworth", "json")` → `file_read(file_id)` |
| "Create a summary of the data in my CSV file" | `file_lookup("csv", "csv")` → `file_read()` → LLM processes → `file_write("summary", summary_content, "txt")` |
| "Delete the file I uploaded earlier" | `file_search("*.txt", user_context)` → identify file_id → `file_delete(file_id)` |

---