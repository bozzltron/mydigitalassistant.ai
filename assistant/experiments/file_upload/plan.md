# Experiment Plan: File Upload Read-Edit Cycle

## Scientific Method Plan

**Question**: Can the agent upload a file, read its contents, and allow natural language edits to the stored data?

**Hypothesis**: Uploading a CSV/text file creates structured frames/slots in memory, and the agent can subsequently read back and edit specific slots via natural language prompts.

**Variables**:
- Independent: File type (CSV vs plain text), file content structure
- Dependent: Agent's ability to recall slot values, apply upsert_slot edits, verify changes persist
- Controls: Same file upload endpoint, same memory store, same LLM client (utility model), same extraction pipeline

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