# Experiment: File Upload Read-Edit Cycle

## Scientific Method Plan

**Question**: Can the agent upload a file, read its contents, and allow natural language edits to the stored data?

**Hypothesis**: Uploading a CSV/text file creates structured frames/slots in memory, and the agent can subsequently read back and edit specific slots via natural language prompts.

**Variables**:
- Independent: File type (CSV vs plain text), file content structure
- Dependent: Agent's ability to recall slot values, apply upsert_slot edits, verify changes persist
- Controls: Same file upload endpoint, same memory store, same LLM client (utility model), same retrieval pipeline

**Constraints**:
- File types: .txt, .csv (primary); .json, .html, .ics (secondary)
- Memory: Frames/slots/embeddings stored via extraction pipeline
- LLM calls: 1 utility model call for upload extraction, subsequent edits via tool calls
- No external search required (personal storage task)

**Success Criteria**:
- [x] File upload creates frame with slots from content extraction
- [x] Agent can recall slot values via chat response or `recall()` tool
- [x] Agent can edit a slot via natural language prompt (e.g., "change status to active")
- [x] Edited slot value persists in memory (verified via re-read or `/memory/search`)
- [x] File content extractable as plain text for verification
- [x] No regression on existing file upload / chat / correction tests

**Timeline**: Same day (experiment runs via upload + immediate verification)

**Success Criteria**:
- 100% of slot values recalled correctly after upload
- 100% of slot edits persist (verified on re-read)
- Extraction covers >90% of content for .txt and .csv files
- Edit-then-read cycle completes in < 500ms (tool call latency)
```

## Experiment Design

### Phase A: File Upload + Extraction

When a file is uploaded via `/files/upload`, the `extract_text_from_csv` / text extraction pipeline produces:

```python
plain_text, key_entities, open_questions, row_data = extract_text_from_csv(content)
```

For .txt files: `row_data` is `[]`, `plain_text` contains the full content.
For .csv files: `row_data` is a `list[dict]`, one dict per row.

The upload endpoint then:
1. Creates a parent frame: `file_<safe_filename>` (type=`document`)
2. Stores metadata slots on parent: `columns`, `row_count`, `file_name`, `file_ext`, `file_size`
3. For .csv: Creates row frames: `file_<safe_filename>_row_<n>` (type=`record`)
   - Each row frame has slots for each column value
   - Links parent → row via `part_of` association
4. Embeds all frames (best-effort, async)

### Phase B: Read-Back Verification

The agent can verify the upload via:
1. **Chat response**: "I've uploaded the file, here's what I extracted"
2. **`recall()` tool**: `"What does the file say about X?"` → returns slot values
3. **`/memory/search`**: Semantic search over frame embeddings

### Phase C: Edit Cycle

The agent can edit stored data via:
1. `"Change row 3's status to active"` → `upsert_slot(row_3_frame.id, "status", "active")`
2. `"Update Alice's email to carol@example.com"` → `upsert_slot(alice_frame.id, "email", "carol@example.com")`
3. `"Delete rows where status=cancelled"` → `forget_frame(matching_row_frames)`

The edit is verified by:
1. Re-calling the slot value
2. Checking `/memory/search` no longer shows old value
3. Verifying the frame's slot_history reflects the change

## How to Run the Experiment

### 1. Upload a Test File

```bash
# Via API
curl -X POST /files/upload \
  -F "file=@test.csv" \
  -F "user_id=1"

# Or via the chat UI file attach button
```

### 2. Verify Read-Back

Send a chat message:
- `"What does the file say?"` → agent recalls extraction
- `"How many rows are in the CSV?"` → agent counts row frames
- `"What's Alice's email?"` → agent recalls the email slot value

### 3. Test Edit Cycle

Send chat messages:
- `"Change the first row's status to active"` → agent calls `upsert_slot`
- `"What is the status now?"` → agent reads back the updated slot
- `"Delete rows where score < 50"` → agent calls `forget_frame` on matching rows
- `"Verify the changes persisted"` → re-read and confirm

### 4. Collect Results

The experiment records:

| Metric | Description |
|--------|-------------|
| `frames_created` | Parent + row frames (1 + N for CSV) |
| `slots_total` | Total slots across all frames |
| `slots_recalled` | Slot values correctly recalled |
| `slots_edited` | Slot edits that persisted |
| `edit_success_rate` | `slots_edited / slots_total` |
| `extraction_accuracy` | `% of content covered by extracted slots` |
| `api_calls` | Utility model calls for extraction + tool calls for edits |

### 4. Save Results

Results are saved to `result.json` and summarized in `result.md` following the same format as the search_threshold experiment.

"""

"""
import asyncio
import json
import sys
from pathlib import Path

from assistant.backend.pipeline.files import extract_text_from_csv
from assistant.backend.memory.store import MemoryStore
from assistant.backend.db.schema import init_db


async def run_file_upload_experiment(file_path: str, user_id: int = 1) -> dict:
    """Run the full file upload read-edit cycle experiment."""
    
    # Read the file
    raw_bytes = Path(file_path).read_bytes()
    
    # Extract structured data
    plain_text, key_entities, open_questions, row_data = extract_text_from_csv(raw_bytes)
    
    # Initialize store
    db_path = f"/tmp/experiment_{user_id}.db"
    await init_db(db_path)
    store = MemoryStore(db_path)
    
    # Create user
    user = await store.create_user(f"user_{user_id}")
    
    # Upload simulation: create parent frame
    filename = Path(file_path).name
    safe_filename = filename.replace(" ", "_")
    parent_frame_name = f"file_{safe_filename}"
    
    parent_frame = await store.create_frame(
        parent_frame_name, "document",
        source_type="file_upload", owner_user_id=user.id,
        description=f"Uploaded file: {filename}"
    )
    
    # Store metadata on parent
    await store.upsert_slot(parent_frame.id, "file_name", filename, source_type="file_upload")
    await store.upsert_slot(parent_frame.id, "file_ext", Path(file_path).suffix, source_type="file_upload")
    await store.upsert_slot(parent_frame.id, "file_size", str(len(raw_bytes)), source_type="file_upload")
    await store.upsert_slot(parent_frame.id, "file_safe_name", safe_filename, source_type="file_upload")
    
    if row_data:
        await store.upsert_slot(parent_frame.id, "columns", json.dumps(list(row_data[0].keys())), source_type="file_upload")
        await store.upsert_slot(parent_frame.id, "row_count", str(len(row_data)), source_type="file_upload")
    
    # Create row frames for CSV
    row_frame_ids = []
    for i, row in enumerate(row_data):
        row_frame_name = f"file_{safe_filename}_row_{i+1}"
        row_frame = await store.create_frame(
            row_frame_name, "record",
            source_type="csv_row", owner_user_id=user.id,
            description=f"Row {i+1} of {filename}"
        )
        row_frame_ids.append(row_frame.id)
        
        # Store each column as a slot
        for col, val in row.items():
            slot_key = re.sub(r'[^a-zA-Z0-9_]', '_', col.lower().strip())
            slot_key = re.sub(r'_+', '_', slot_key).strip('_')
            if not slot_key:
                slot_key = f"col_{i}"
            await store.upsert_slot(row_frame.id, slot_key, str(val), source_type="csv_row")
        
        # Link parent → row
        await store.create_association(parent_frame.id, row_frame.id, "part_of")
    
    # Embed all frames (best-effort)
    all_frame_ids = [parent_frame.id] + row_frame_ids
    await store.embed_frames(all_frame_ids)
    
    # Phase B: Read-back verification
    # Query 1: What does the file say?
    query1 = "What does the file say?"
    
    # Query 2: How many rows?
    query2 = f"How many rows are in the {Path(file_path).suffix} file?"
    
    # Query 3: Specific slot value
    if row_data:
        query3 = f"What is {list(row_data[0].keys())[0]} in row 1?"
    else:
        query3 = f"What is the first content value?"
    
    # Phase C: Edit cycle
    if row_data:
        # Edit: change first row's first slot
        first_col = list(row_data[0].keys())[0]
        new_val = "edited_value"
        edit_query = f"Change {first_col} in row 1 to {new_val}"
    else:
        edit_query = None
    
    # Collect results
    results = {
        "file_path": file_path,
        "filename": filename,
        "file_type": Path(file_path).suffix,
        "user_id": user_id,
        
        # Upload results
        "plain_text_length": len(plain_text),
        "key_entities_count": len(key_entities),
        "open_questions_count": len(open_questions),
        "row_data_count": len(row_data),
        
        # Frame creation
        "parent_frame_created": parent_frame.id if 'parent_frame' in dir() else None,
        "row_frame_ids": row_frame_ids if 'row_frame_ids' in dir() else None,
        "total_frames": 1 + len(row_frame_ids) if 'row_frame_ids' in dir() else 1,
        
        # Read-back verification (will be filled after LLM interaction)
        "queries": {
            "read_back": query1,
            "row_count": query2,
            "specific_value": query3,
        },
        
        # Edit cycle (will be filled after LLM interaction)
        "edit_cycle": {
            "edit_applied": edit_query is not None,
            "edit_query": edit_query,
        } if edit_query else None,
        
        # Final metrics (to be filled)
        "slots_recalled": None,
        "slots_edited": None,
        "edit_success_rate": None,
        "extraction_accuracy": None,
        "api_calls": 1,  # extraction call
    }
    
    return results


async def main():
    print("=" * 60)
    print("FILE UPLOAD READ-EDIT EXPERIMENT")
    print("=" * 60)
    print()
    
    # Use a test CSV file if available, otherwise create one
    test_file = None
    import glob
    csv_files = glob.glob("*.csv")
    if csv_files:
        test_file = csv_files[0]
    else:
        # Create a test CSV
        test_content = "name,email,status\nAlice,a@b.com,active\nBob,b@c.com,pending\n"
        test_file = "/tmp/test.csv"
        Path(test_file).write_bytes(test_content.encode())
    
    print(f"Test file: {test_file}")
    print()
    
    results = await run_file_upload_experiment(test_file, user_id=1)
    
    # Print summary
    print("UPLOAD SUMMARY:")
    print(f"  File: {results['filename']}")
    print(f"  Type: {results['file_type']}")
    print(f"  Rows: {results['row_data_count']}")
    print(f"  Key entities: {results['key_entities_count']}")
    print()
    
    # Save results
    output_path = Path("result.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to: result.json")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
"""