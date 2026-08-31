# Background Conversation Summarization Plan

## Overview
Add a background summarization job that periodically compresses conversation episodes into structured frame summaries. This enables the agent to "learn slowly but effectively" by compressing raw episodic memory into structured semantic frames, reducing prompt pressure while preserving long-term knowledge.

## Alignment with Design Principles

| Principle | Application |
|-----------|-------------|
| **Model-first correction** | Use utility model for summarization (not scripted rules) |
| **No templated responses** | Summaries are model-generated narratives, not templates |
| **Lean on model flexibility** | Utility model decides what's salient, not hardcoded rules |
| **Scheduled tasks are memory** | Summarization runs as a scheduled task; output is frames/slots |
| **Clean ship** | New module with tests; no dead code |
| **Stability: no regressions** | Regression test for summarization pipeline |

## Requirements

### Functional Requirements
1. **Periodic summarization**: Run as a scheduled task (configurable interval, default daily)
2. **Session-scoped**: Summarize episodes per session; produce one summary frame per session
3. **Model-driven**: Use utility model (qwen2.5:3b) for summarization — cheap, fast, already loaded
4. **Structured output**: Generate frames with slots: `summary`, `key_entities`, `open_questions`, `session_id`, `turn_count`, `date_range`
4. **Frame integration**: Summaries stored as frames (`conversation_summary_{session_id}`) with embeddings for retrieval
5. **Episodic linkage**: Link summary frame to source episodes via associations (`summarizes` relation)
6. **Idempotent**: Re-running on same session updates existing summary frame (upsert)

### Non-Functional Requirements
1. **Off-hot-path**: Runs in scheduler background; never blocks chat response
2. **Timeout-safe**: Utility model call has 60s timeout; max 3 retries
3. **Configurable**: Interval, max sessions per run, min turns to trigger via `.env`
4. **Observable**: Logs `summary_created` / `summary_updated` with turn count, char count
5. **Testable**: Unit + integration tests for summarization pipeline

### Configuration (`.env`)
```bash
SUMMARIZATION_ENABLED=true
SUMMARIZATION_INTERVAL_HOURS=24          # daily
SUMMARIZATION_MIN_TURNS=10               # minimum turns before summarizing
SUMMARIZATION_MAX_SESSIONS_PER_RUN=5     # limit per scheduler tick
SUMMARIZATION_MAX_CHARS=4000             # max input chars to utility model
SUMMARIZATION_TIMEOUT_SECONDS=60
```

## Architecture

### New Files
```
assistant/backend/scheduler/summarizer.py      # Core summarization logic
assistant/backend/scheduler/__init__.py        # Export public API
```

### Modified Files
```
assistant/backend/config.py                    # New Settings fields
assistant/backend/scheduler/runner.py          # Register summarization task
assistant/backend/memory/store.py              # Helper: upsert_summary_frame
assistant/backend/memory/retrieval.py          # Include summary frames in retrieval
```

### Database Schema (no migration needed)
- Uses existing `frames` table: `name="conversation_summary_{session_id}"`
- Slots: `summary`, `key_entities`, `open_questions`, `session_id`, `turn_count`, `date_range`, `created_at`, `updated_at`
- Associations: `summarizes` relation from summary frame → episode frames

## Implementation Steps

### Phase 1: Core Summarizer Module (Week 1)
1. Create `assistant/backend/scheduler/summarizer.py` with:
   - `Summarizer` class with `summarize_session(session_id, user_id)` method
   - Prompt template for utility model
   - Output parsing → structured slots
   - Frame upsert via `MemoryStore`
2. Add `assistant/backend/scheduler/__init__.py`
3. Unit tests for summarizer logic

### Phase 2: Scheduler Integration (Week 1)
1. Add config fields to `Settings` in `config.py`
2. Register summarization task in `scheduler/runner.py` (daily timer)
3. Add task to scheduler loop alongside GC/consolidation
4. Integration test: scheduler runs summarizer → creates frame

### Phase 3: Retrieval Integration (Week 1)
1. Update `Retriever.retrieve()` to include summary frames in memory context
2. Add `summary` frames to formatted context (new "## Conversation Summaries" section)
3. Ensure summaries have embeddings (consolidation job handles)

### Phase 4: Tests & Polish (Week 2)
1. Unit tests: `test_summarizer.py`
2. Integration test: `test_summarization_e2e.py` (create session → run summarizer → verify frame → retrieve)
4. Regression test: summarization doesn't block chat
5. Update AGENTS.md with summarization docs

## Success Criteria
- [ ] Summarization runs daily without blocking chat
- [ ] Summary frames appear in retrieval for relevant queries
- [ ] Prompt size stays under `max_system_prompt_chars` for 50+ turn conversations
- [ ] All existing tests pass + new tests pass
- [ ] Logs show `summary_created` / `summary_updated` with metrics

## Risk Mitigation
| Risk | Mitigation |
|------|------------|
| Utility model timeout | 60s timeout + 3 retries; skip session on failure |
| Prompt overflow | Truncate input to `SUMMARIZATION_MAX_CHARS` (4000) |
| Duplicate summaries | Upsert by session_id; idempotent |
| Prompt cache pollution | Summaries retrieved separately, not in hot prompt path |

## Timeline
| Phase | Duration | Deliverable |
|-------|----------|-------------|
| 1. Core | 2 days | `summarizer.py` + unit tests |
| 2. Scheduler | 1 day | Scheduler integration |
| 3. Retrieval | 1 day | Retrieval integration |
| 4. Tests | 1 day | Full test coverage + AGENTS.md update |
| **Total** | **~5 days** | **Production-ready** |

---

## Appendix: Utility Model Prompt Template
```
Summarize the following conversation session into a structured summary.

Session ID: {session_id}
Turns: {turn_count}
Date Range: {first_timestamp} to {last_timestamp}

Conversation:
{episodes_text}

Produce a JSON object with these fields:
- "summary": 3-5 sentence narrative capturing key topics, decisions, and outcomes
- "key_entities": list of important people, projects, concepts mentioned
- "open_questions": list of unresolved topics or follow-ups needed
```