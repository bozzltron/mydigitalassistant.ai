# Phase 7 Plan — Scheduled Tasks That Actually Work

**Status:** in planning
**Last updated:** 2026-08-21
**Depends on:** Phase 6 fleet (M2 gate covers scheduled-extraction fixtures; §6.5.1 replaces cron parsing)
**Trigger:** User asked the agent "are you capable of scheduled tasks / how do I set one up" → failed.

---

## 1. Diagnosis — Why Your Attempt Failed

Traced through the live code paths. Three compounding defects, all reproducible:

1. **Capability questions get hijacked as task commands.**
   `task_router.py:42` `SCHEDULED_TASK_PATTERNS` includes bare `\bschedule(d)?\b`. The
   meta-question *"can you set up scheduled tasks?"* contains "scheduled" → classified
   `TaskType.SCHEDULED` → `_handle_scheduled_task()` runs field-extraction on what is a
   *question*, produces garbage (`ValueError` from `parse_schedule`) → user gets
   "I had trouble parsing that schedule". Asked without the keyword, the same question
   routes to functional chat where…
2. **The agent has no self-model.**
   `build_system_prompt()` (`llm_client.py:126`) injects memory context but never states
   what the assistant *can do*. There are no capability frames in memory either. So
   "are you capable of X?" → truthful-sounding "no / I don't know". The agent cannot
   describe its own features.
3. **Even success is a lie while `SCHEDULER_ENABLED=false` (the default).**
   `config.py:46` defaults off; docker-compose passes `${SCHEDULER_ENABLED:-false}`.
   Creation responds *"Done! …Next run: …"* regardless — then nothing ever fires because
   the runner loop was never started. No warning, no status anywhere.

Bonus rot found while tracing: `task_router.py:56` contains the literal pattern
`\bfollow artificial intelligence in the news\b` — proof the heuristic list grows by
hardcoding individual user requests. Also all times are **UTC-hardcoded**
(`cron.py:18`) — "daily at 9am" fires at 9am UTC, which is a different 9am than the
household's.

---

## 2. Fixes

### 7.1 Give the agent a capability self-model
- Add a static `CAPABILITIES` block to `build_system_prompt()`: scheduling/reminders,
  memory+recall, corrections, web search, feedback, brain export/import, voice. Meta-
  questions become answerable and routable.
- Seed capability frames into memory at init (type `capability`) so introspective recall
  ("what can you do?") retrieves them like any other knowledge.
- New intent in scheduled-field schema: `intent=help` → respond with usage examples
  instead of attempting extraction. Router distinguishes **scheduled_command** vs
  **scheduled_question** before entering the handler.

### 7.2 De-brittle the router
- Freeze `SCHEDULED_TASK_PATTERNS` (and delete the AI-news line). Narrow heuristics to
  high-precision verbs (`remind me to…`, `set up…`), push everything ambiguous to the
  utility-model classifier (4-way: functional / introspective / scheduled_command /
  scheduled_question). This formalizes Phase 6 §6.5's "freeze, don't grow" rule.
- Regression fixtures: the exact utterances that failed today, plus near-misses
  ("what reminders do I have?", "delete the briefing task").

### 7.3 Cron parsing → LLM-first (Phase 6 §6.5.1 lands here)
- `extract_scheduled_task_fields` returns structured fields including ISO-ish schedule;
  new `cron_from_fields()` asks utility model for a cron expression (JSON mode),
  validated by `croniter`; `parse_schedule()` demoted to offline fallback.
- Missing fields (time? what to run?) → **one** clarifying question, never silent defaults.

### 7.4 Timezone reality
- `TIMEZONE` setting (default: system local). Store tz-aware timestamps, render local
  times in all responses/lists, handle DST via zoneinfo. Kids' bedtimes in UTC is a bug,
  not a quirk.

### 7.5 Honest lifecycle + visibility
- Creation response states scheduler state: running / OFF (+ how to enable).
- `/health` reports `scheduler_running`, next due task; CLI `assistant tasks
  list/add/pause/resume/run/delete` wrapping the existing REST endpoints.
- Surface `last_result_summary` + `last_run` in every listing (data already stored).

### 7.6 Create-flow confirmation
- Echo back: name, human schedule **in local time**, first run, prompt summary.
  Slug names from the prompt (not `task_a3f2c1`); require explicit confirm only when
  fields were guessed.

---

## 3. Milestones

- **S1 — Stop lying (routing + self-model).** 7.1 + 7.2. Gate: today's failing
  utterances pass end-to-end tests; no behavior change for valid commands.
- **S2 — Honesty about execution.** 7.5. Health/CLI/status surfaces; docs update in
  `assistant/AGENTS.md` scheduled-tasks section.
- **S3 — Smart parsing + local time.** 7.3 + 7.4 + 7.6, using the Phase 6 utility model.
- **S4 — Fire-forget proof.** E2E in `docker-compose.test.yml`: create 5-min-interval
  task → observe ≥2 real firings → recall result via chat → delete. Injected clock for CI speed.

## 4. Acceptance Criteria

- [ ] "Are you capable of scheduled tasks?", "How do I set one up?", "What reminders do I have?" all answered correctly (not hijacked, not denied)
- [ ] Created task fires on schedule in CI with scheduler enabled; creation reply flags OFF state when disabled
- [ ] All schedule times rendered in configured local timezone incl. DST-safe storage
- [ ] Cron generation via utility model passes parity fixtures; fallback parser still green
- [ ] `./run_ci.sh` green (plain + encrypted); `assistant/AGENTS.md` updated
