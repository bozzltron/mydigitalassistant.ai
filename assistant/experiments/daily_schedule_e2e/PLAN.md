# Experiment: Atomic Daily Scheduled Tasks End-to-End

## Status

**Superseded.** The `POST /tasks/run-due` endpoint this specified shipped in
`main.py`, and the full loop — create task → run-due → query the report — is
covered by `assistant/tests/test_daily_schedule_experiment.py`
(`test_daily_task_full_loop`). No separate `result.md` is written; the code and
its test are the record.

## Goal
Prove the full loop: natural language → scheduled task stored in memory → manual run via API → task executes through full cognitive loop → generates queryable memories → user asks "what did you learn today" → agent recalls and reports.

---

## Phase 1: Infrastructure (Minimal, Reuses Existing)

### 1.1 Add `POST /tasks/run-due` endpoint
- **File**: `assistant/backend/main.py`
- **Logic**: Call `store.get_due_scheduled_tasks()` → for each, call `orchestrator.run_scheduled_task()` → return summaries
- **Reuses**: Existing `_execute_task` logic in runner, `run_scheduled_task` in orchestrator
- **Response**: `{ "tasks_run": [{"name": "...", "result_summary": "...", "success": true}] }`

### 1.2 Ensure retrieval finds daily-run events
- **File**: `assistant/backend/memory/retrieval.py` 
- **Check**: `Retriever.retrieve()` already searches frames + episodes + associations
- **Verify**: Daily run event frame (`daily_run_YYYY_MM_DD`) with `includes_task` associations and linked episodes are retrieved for "what did you learn today" queries
- **May need**: Boost event frames in retrieval ranking, or add "daily run" to query expansion

---

## Phase 2: Natural Language Task Creation (Already Works)

### 2.1 Verify `extract_scheduled_task_fields` handles the target prompt
- **Input**: "hey let's read reputable tech news every day and keep an eye on breakthroughs in artificial intelligence"
- **Expected extraction**: 
  - `intent: "create"`
  - `name: "tech_news_briefing"` (or similar)
  - `repeat: true`
  - `prompt: "Read reputable tech news and report on AI breakthroughs"`
  - `description: "Daily tech news briefing focusing on AI breakthroughs"`

### 2.2 Test via chat endpoint
- User sends message → router classifies as `SCHEDULED` → `_handle_scheduled_task` creates task
- Verify task stored as `scheduled_task` frame with proper slots

---

## Phase 3: Manual Execution & Memory Generation

### 3.1 Trigger via `POST /tasks/run-due`
- Fires due tasks (or all if testing with forced due)
- Each task runs through `orchestrator.run_scheduled_task()`:
  - Retrieval for task prompt
  - Search (if reasoner decides)
  - LLM generates response
  - Episode logged with `session_id="scheduled-{task_name}-{date}"`
  - **Extraction from response** → facts stored in memory with `source_type="scheduled_task"`
  - Daily run event frame created/updated with `tasks_run` + `status`
  - Associations: task → `ran_in` → daily_run; daily_run → `includes_task` → task; task → `last_output_episode_id`

### 3.2 Verify memory artifacts created
- Assistant episode (queryable)
- Extracted fact frames/slots (e.g., `ai_breakthrough_2024_q3`, `new_model_release`)
- Daily run event frame with associations
- Task frame updated: `last_run`, `last_result_summary`, `next_run`

---

## Phase 4: Report Query & Recall

### 4.1 User asks: "what did you learn in daily tasks today. Give me a report"
- Router classifies as `INROSPECTIVE` (recall)
- Retrieval searches for "daily tasks today learn"
- Should match:
  - Daily run event frame (`daily_run_2026_09_12`)
  - Associated task episodes
  - Extracted fact frames from task execution

### 4.2 Agent generates report from memory context
- System prompt includes retrieved frames/episodes
- Chat model synthesizes natural language report
- Response includes citations to episodes/facts

---

## Phase 5: Test & Verification

### 5.1 New test file: `assistant/tests/test_daily_schedule_experiment.py`
```python
async def test_daily_task_full_loop():
    # 1. Create task via chat
    # 2. Force task due (or call run-due)
    # 3. Execute via POST /tasks/run-due
    # 4. Verify memory artifacts: episode, extracted facts, daily_run event
    # 5. Query "what did you learn today" via chat
    # 6. Verify response references task output + extracted facts
```

### 5.2 Run existing regression tests
- `pytest assistant/tests/test_daily_schedule.py`
- `pytest assistant/tests/test_review_fixes.py`

### 5.3 Manual verification script
- `./assistant/bin/assistant chat` → add task → run-due → ask for report

---

## Architecture Notes (No Over-Engineering)

| Component | Approach | Rationale |
|-----------|----------|-----------|
| Task creation | Existing `extract_scheduled_task_fields` + chat | Already implemented, model-driven |
| Task storage | `scheduled_task` frame + slots | Design principle: "tasks are memory" |
| Execution | `orchestrator.run_scheduled_task()` | Full cognitive loop, same as chat |
| Memory output | Episodes + extracted facts + daily_run event | Already happens in runner |
| Report query | Standard retrieval (no new endpoint) | Daily runs are queryable memory |
| Manual trigger | `POST /tasks/run-due` (thin wrapper) | Exposes existing scheduler logic |

---

## Files to Modify

1. **`assistant/backend/main.py`** - Add `POST /tasks/run-due` endpoint (~20 lines)
2. **`assistant/backend/memory/retrieval.py`** - Verify/boost daily-run event retrieval (~0-10 lines)
3. **`assistant/tests/test_daily_schedule_experiment.py`** - New integration test (~80 lines)

---

## Success Criteria

- [ ] User can say "read tech news daily and watch for AI breakthroughs" → task created
- [ ] `POST /tasks/run-due` executes task, returns summary
- [ ] Task execution creates: assistant episode, extracted fact frames, daily_run event
- [ ] User asks "what did you learn today" → agent reports specific findings from task
- [ ] All existing tests pass
- [ ] New experiment test passes

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Retrieval misses daily_run event | Add "daily run" to query or boost event frames in retriever |
| Extraction from task response fails | Already has try/except + logging; extraction is best-effort |
| Task prompt too vague for search | Reasoner decides search_needed; extraction runs regardless |
| Timezone issues in "today" | Daily run frame name uses UTC date; retrieval uses user's query time |

---

## Requirements (Enriched)

### Functional Requirements
1. **FR-1**: User can create a daily scheduled task via natural language chat
2. **FR-2**: Task is stored as a `scheduled_task` frame with slots (prompt, frequency, enabled, next_run, last_run, description)
3. **FR-3**: `POST /tasks/run-due` endpoint triggers execution of all due tasks immediately
4. **FR-4**: Task execution runs through the full cognitive loop (retrieval → search → reasoning → LLM → extraction)
5. **FR-5**: Task execution creates an assistant episode linked to a session `scheduled-{task_name}-{date}`
6. **FR-6**: Task response is processed for fact extraction; facts stored with `source_type="scheduled_task"`
7. **FR-7**: Daily run event frame (`daily_run_YYYY_MM_DD`) created/updated with task associations
8. **FR-8**: User can query "what did you learn today" and receive a report synthesized from memory

### Non-Functional Requirements
1. **NFR-1**: No new database schema changes (uses existing frames/slots/associations/episodes)
2. **NFR-2**: No new scheduler logic (reuses existing `run_scheduled_task` and runner logic)
3. **NFR-3**: API endpoint completes within 30s per task (existing timeouts apply)
4. **NFR-4**: Retrieval uses existing semantic search (no dedicated report endpoint)
5. **NFR-5**: All existing tests continue to pass

### Test Requirements
1. **TR-1**: Integration test covering full loop (create → run → query)
2. **TR-2**: Regression tests for `test_daily_schedule.py` and `test_review_fixes.py` pass
3. **TR-3**: Manual verification via CLI works end-to-end