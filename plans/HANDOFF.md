# Handoff: Phase 11 Ready for Implementation

## Context

This handoff follows a code review and bug-fix session. The project is a privacy-first, local AI assistant with memory, scheduled tasks, web search, and voice mode. We identified regressions in voice transcription, scheduled-task `run_now`, and empty-response handling, then reviewed the project against its own design principles from an HCI perspective.

## What was just done

### Code fixes (already on `main`)

1. **Voice mode race fix** (`assistant/backend/static/chat.html`)
   - Added `_stopping` guard in `stopRecording()` to prevent double-stop.
   - Moved `loudFrameCount` / `recordingStartTime` cleanup into `stopRecording()`.
   - Added `[voice]` logging to silence detection for observability.

2. **Scheduled task `run_now` fix** (`assistant/backend/pipeline/orchestrator.py`)
   - `_handle_scheduled_task` now calls `update_scheduled_task_run()` after `run_scheduled_task()`.
   - This fixes: `last_run` not updating, and `once` tasks not being disabled after manual run.

3. **Empty response fallback** (`assistant/backend/pipeline/orchestrator.py`)
   - `run_scheduled_task` falls back to `"Task completed."` if model returns empty content/thinking.
   - Main chat path falls back to `"I'm not sure how to respond to that."` with a warning log.

4. **Test fixes**
   - Fixed `assistant/tests/test_search_tool.py` importing `ChatRequest` from `main.py` (forced `python-multipart` loading).
   - Added `test_chat_run_now_task` and `test_chat_run_now_disables_once_task`.
   - Added 5 `merge_extractions` regression tests.
   - Added `test_orchestrator_correction_contradicted_flagged_not_applied`.

5. **Design principles** (`AGENTS.md`, `assistant/AGENTS.md`)
   - Added **Clean Ship** and **Stability: no regressions while adding features**.

### Test status

- `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py` — **all pass**.
- `pytest assistant/tests/test_orchestrator.py assistant/tests/test_extractor.py` — **all pass**.
- `ruff check assistant/backend/ assistant/cli/ assistant/tests/` — **clean**.
- Full local suite: 438 passed, 2 pre-existing failures in `test_security.py` (local `.env` file triggers security check; not a code issue).

## New plan

The new phase plan is:

```
plans/PHASE_11_HCI_STABILITY_PLAN.md
```

It covers 11 requirement areas:

1. **Resolve "No templated responses" violations** — remove hardcoded correction confirmations and LLM-unreachable fallback.
2. **Calibrated uncertainty in chat** — show confidence/source tags on learned facts and in memory-based answers.
3. **Accessible by default** — `aria-label`s, `prefers-reduced-motion`, focus rings, landmarks.
4. **Consent-by-context for Brave Search** — first-per-session disclosure when queries leave the device.
5. **Household safety mode** — per-user moderation layer.
6. **User erasure and control** — delete messages, forget facts, clear session from UI.
7. **Explainable inference** — show plan summary and model `thinking` in trace panel.
8. **Voice mode robustness** — audio-level meter, manual stop, stall hint.
9. **Progressive disclosure** — trace panel hidden by default.
10. **Local-first cleanup** — vendor 3D libs instead of loading from `unpkg.com`.
11. **Clean ship and stability enforcement** — CI gates for unused imports and critical-path tests.

Recommended implementation order is in the plan.

## Important constraints

- **All LLM inference must stay local (Ollama).** No cloud APIs.
- **Default search must stay local (SearXNG).** Brave is opt-in only.
- **FastAPI binds 127.0.0.1 only.**
- **No telemetry.**
- **Every non-trivial change needs a regression test.**
- **Run `ruff check --select=F401,F811` and the critical-path tests before considering work done.**

## First task for the next agent

Start with **R1: Resolve "No templated responses" violations** and **R11: Clean Ship / Stability enforcement**.

These are the highest-priority because:
- R1 resolves an active contradiction with the project's own design principles.
- R11 establishes CI gates that protect every subsequent change.

After those, proceed in the order listed in the plan.

## Key files to know

- `assistant/backend/static/chat.html` — main UI; voice mode, trace panel, learned indicator, settings.
- `assistant/backend/static/brain.html` — Brain Observatory; 3D graph loads external libs.
- `assistant/backend/pipeline/orchestrator.py` — cognitive loop, correction handling, response generation.
- `assistant/backend/pipeline/extractor.py` — correction extraction/validation/application, search extraction.
- `assistant/backend/pipeline/llm_client.py` — model client; parses `thinking` field.
- `assistant/backend/pipeline/search.py` — search backends, query sanitization.
- `assistant/backend/memory/store.py` — memory CRUD, scheduled-task helpers.
- `assistant/backend/main.py` — FastAPI endpoints, `/correction` response.
- `assistant/tests/` — tests; critical paths are in `test_daily_schedule.py`, `test_review_fixes.py`, `test_orchestrator.py`, `test_extractor.py`.

## Contact / questions

If anything in the plan conflicts with the security constraints in `AGENTS.md`, follow `AGENTS.md`. When in doubt, add a regression test and ask.
