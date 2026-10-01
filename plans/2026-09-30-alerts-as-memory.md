---
date: 2026-09-30
status: done
estimated_hours: 14
---

# Plan C — Alerts as memory: the agent's channel to the user

## Objective

Give the agent a way to tell the user something when the user was not there to hear it,
and to open a conversation so the thing gets resolved rather than merely filed.

**An alert is memory of a type.** Not a notification queue that happens to link to a frame —
the alert *is* the memory object, and the UI is a view over it. Same principle as
"Scheduled tasks are memory": no hidden table, the agent's own memory is the source of
truth.

## Why this matters (the goal, applied)

AGENTS.md: the assistant builds an accurate model of the user's world by triangulating the
user and the internet, and helps without the user repeating themselves.

Alerts as memory serves that directly in a way a notification table cannot:
**because an alert is memory, retrieval can surface it when it is contextually relevant.**
If the user is mid-conversation about their submission list, the agent can raise the alert
it already holds about a related conflict. The bell is one delivery path; the cognitive
loop is the other, and the second is the more valuable one.

## The contract: when is an alert warranted?

> **An alert is warranted when the agent learned something and the user was not there to
> hear it.**

Not "the agent learned something" — that happens constantly. This is the mistake the
current system makes: 22 `search_result` alerts fired mid-conversation, when the user was
watching it learn.

| Situation | Correct behaviour |
|---|---|
| Agent learns during a live conversation | **Say it in the conversation.** No alert. |
| Agent learns during scheduled work, user absent | **Alert.** The user was not there. |
| Agent finds a conflict it cannot resolve | **Alert** — a request, not a report. |
| Task completes | **Not an alert.** Mechanism, not a message. |

So an alert is always *the agent resuming a conversation the user was absent from.*

## Measured state (live brain, 2026-09-30, after the manual task run)

```
alerts: 103, all unread
  task_result:   54   "Task completed: job_postings_monitor"   ← mechanism, not a message
  search_result: 22   "New facts learned from search"          ← fires mid-conversation
  conflict:      19   "Search conflict auto-resolved"
  correction:     6
  task_alert:     2   ← WORKS (see below)
oldest: 2026-09-20    all 103 still unread
```

The channel is not broken; it is **full of the wrong kind of thing**. `is_read` is a passive
flag and nobody reads a notification that accomplishes nothing on reading. One manual task
run added 13 `task_result` rows and 2 `task_alert` rows — the ratio is the whole argument for
this plan.

### `task_alert` is verified working

It reported 0 for weeks, which was explained by the scheduler having been off
(`SCHEDULER_ENABLED=false`, heartbeat frozen at `2026-09-23T17:26:36`). A manual run on
2026-09-30 then fired it twice, and the output is exactly what the contract promises:

```
"Restock Alert"        important   "The Zelda console restocked just now — limited
                                    quantities available before they sell out again."
"TechCrunch coverage…" important   "…exhibit table deadline ending Sept 25 — register
                                    soon for up to $200 ticket savings…"
```

Both are the agent telling the user something it learned **while the user was absent** — the
presence rule, working unmodified. `_extract_agent_alert` (`runner.py:113`) reads a line
starting `ALERT:`, treats the rest as the body, and strips the footer from the stored
summary; the directive is issued in `orchestrator.py:224-247`. It was **never broken, only
never exercised.**

## Phases

### Phase 1 — Alert as a memory object (~4 h)

1. An alert is a frame of type `alert`, with slots:
   - `message` — what the agent wants to say
   - `reason` — why it is worth the user's attention
   - `severity` — `info` | `important`
   - `status` — `new` | `resolved`
   - `about` — what it concerns (a frame id / name), so resolution has a target
   - `origin` — which task/session produced it
2. **`alerts` table removed.** Export its rows first (**re-count at execution time** — it
   was 103 when this plan was written and grows with every task run), per the Clean ship
   principle and the existing history.
3. **Consolidation must exclude `alert` frames.** Merge-on-similarity is right for entities
   and clearly wrong for "things the agent wants to ask you" — two unrelated questions
   would fuse into one.
4. Retrieval may surface `new` alerts, so the agent can raise one contextually.

**Acceptance:** retrieving on a topic an open alert concerns can return that alert; no
`alerts` table remains; consolidation never merges two alert frames.

### Phase 2 — The alert is the first message in a conversation (~4 h)

This is the core interaction, and it inverts the normal flow: **the agent initiates.**

1. The alert frame becomes the opening message of a conversation, carrying its own
   resolution instructions — *message passing the agent sends to itself*, with the user
   as the tiebreaker.
2. The user replies; the agent resolves its own memory in that conversation.
3. **Resolution closes the alert, two ways** (belt and braces, because relying only on the
   model remembering is the failure we have already been bitten by):
   - the agent's own instruction in the alert prompt, and
   - a deterministic backstop: when the slot the alert is `about` changes in that
     conversation, the alert is marked `resolved`.
4. **Resolution writes to `slot_history`** like any other belief change — it is the audit
   trail for "why did the agent decide X".

**Acceptance:** an alert conversation produces a first-message-from-agent; resolving the
subject marks the alert `resolved`; a `slot_history` row records it.

### Phase 3 — The UI: a bell, a menu, a selector (~4 h)

Extends the existing `AlertsPanel.tsx` / `TopBar.tsx` (both exist with tests).

1. **Bell in the top bar** showing the count of **new** alerts, changing colour when the
   count is above zero.
2. **Menu on click** listing new alerts, each readable.
3. **Per alert, a conversation selector** — "Resolve this in: [conversation ▾]". Existing
   conversations, plus **New conversation** as the last option. Creating one is already a
   single call (`POST /conversations` → `store.create_session`, returns a `conv_<hex>` id),
   so the fallback path is effectively free; it is listed last because the default is to
   resolve where the user already is.
4. **The agent may guess the venue.** Because the alert is memory and therefore
   retrievable, the agent can offer to resolve it inline in the conversation the user is
   already in. The selector exists for when the user wants to choose.
5. Closing an alert means resolving it, never merely reading it. `is_read` as a concept
   does not survive into the new model.

**Acceptance:** the bell counts new alerts only; selecting a conversation opens it seeded
with the alert; resolving closes the alert and the badge decrements.

### Phase 4 — Quiet the mechanism (~2 h)

1. `task_result` ("Task completed: …") and per-turn `search_result` notices are **not
   alerts** — a task finishing is not something the agent wants to tell you, and a fact
   learned mid-conversation was just said out loud. They become trace/status events
   (the existing trace panel), not bell entries. One task run produces 13 of these against
   2 real alerts; the noise is what buries the signal.
2. `task_alert` needs **no repair** — verified working, see above. Phase 4 only stops
   `task_result` from competing with it.

**Shipped.** Two writers were removed and one was kept with its reasoning recorded:

- `Orchestrator._create_learning_alerts` is now a documented no-op. Nothing is lost:
  the response carries `extraction_summary` and `search_extraction_summary`, and
  `Message.tsx` renders both as "What I learned" / "Found from search", itemised per
  slot with a conflict flag. The bell entry was a third copy of on-screen content.
- Task **completion** no longer alerts; the output is already an episode, and the run
  is recorded as memory (daily-run frame, `last_run`, heartbeat).
- Task **failure** keeps its alert, as `task_failure` rather than `task_result`. This
  is not an exception to the presence rule but the rule applied properly: the user
  asked for a recurring task and it is silently broken, which they were not there to
  see and only the agent knows. `task_failure` keeps the distinction queryable.

**Acceptance:** a task run produces no `task_result` alert; a task whose output ends
`ALERT: …` still produces a `task_alert` frame with the footer removed from the summary
(pinned in `test_daily_schedule.py::test_extract_agent_alert_parses_footer`).

## What this plan does not do

- **Does not build an inbox.** No folders, no read/unread triage, no archive. An alert is
  an open question; answering it closes it.
- **Does not implement conflict reasoning.** That is Plan D. This plan provides the
  escalation target D writes into.
- **Does not add a frames column or a new table.** Reuses existing frame/slot machinery.

## Requirements

**Functional**

| # | Requirement | Phase |
|---|---|---|
| R1 | An alert is a frame of type `alert` with slots `message`, `reason`, `severity`, `status`, `about`, `origin`. | 1 |
| R2 | `status` is `new` or `resolved`. Two states only — the UI needs exactly one bit to stop alerting. | 1 |
| R3 | The `alerts` table is removed after exporting its rows. | 1 |
| R4 | Consolidation never merges two `alert` frames. | 1 |
| R5 | Retrieval can surface a `new` alert when contextually relevant, so the agent may raise it mid-conversation. | 1 |
| R6 | The alert becomes the **first message** of a conversation, role assistant, carrying its own resolution instructions. | 2 |
| R7 | The user's reply lets the agent resolve its own memory in that conversation. | 2 |
| R8 | Resolution marks the alert `resolved`, by the agent's instruction **and** by a deterministic backstop watching the slot the alert is `about`. | 2 |
| R9 | Resolution writes a `slot_history` row like any other belief change. | 2 |
| R10 | Bell shows the count of `new` alerts and changes colour when above zero. | 3 |
| R11 | Clicking the bell lists new alerts, each readable. | 3 |
| R12 | Each alert offers "Resolve this in: [conversation ▾]" — existing conversations, with "New conversation" last. | 3 |
| R13 | The agent may offer to resolve an alert inline in the conversation the user is already in. | 3 |
| R14 | `task_result` and per-turn `search_result` are no longer alerts; they become trace/status events. | 4 |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | Alerts are memory, not a queue: retrieval is a delivery path equal to the bell. |
| N2 | No templated alert content — the message is model-written, not a fixed string per event kind. |
| N3 | Closing an alert means resolving it, never merely reading it. `is_read` does not survive. |
| N4 | Alert content stays local. A conflict alert is the agent *asking before searching*; it never sends data itself. |
| N5 | The existing `AlertsPanel` stays functional until Phase 3 switches its data source — no window where the bell shows nothing. |

**Constraints**

- **Depends on Plan B** — alert frames must not become conflict noise.
- **Does not replace the conflict queue** (see Plan D). A conflict escalated to an alert
  links via `about` and does not duplicate it.
- **Counts move.** Re-count alert rows at execution time; they grow with every task run.

## Dependencies and ordering

- **Depends on Plan B** — alert frames must not become conflict noise.
- **Blocks Plan D** — D escalates unresolvable conflicts through this channel.
- **Independent of Plans A and E.**
- Within the plan: 1 → 2 → 3 → 4. Phase 3 is the UI and must follow Phase 2, since the
  conversation selector depends on the alert-as-first-message behaviour.

## Test strategy

- `test_alert_is_frame_not_table.py` — an alert is created as a frame of type `alert`;
  no `alerts` table exists.
- `test_alert_consolidation_excluded.py` — two similar alert frames are never merged.
- `test_alert_starts_conversation.py` — the alert is the first message, role assistant.
- `test_alert_resolved_by_subject_change.py` — changing the slot the alert is `about`
  marks it `resolved` without the model being asked.
- `test_alert_resolution_writes_history.py` — a `slot_history` row records the change.
- `test_task_completion_is_not_an_alert.py` — a completed task creates no bell entry.
- `test_task_alert_footer_roundtrip.py` — an `ALERT:` footer becomes a `task_alert` and is
  stripped from the stored summary (the path that has never executed).

Gates: `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py`,
then the full suite, then `ruff check .`.

## Principle alignment

| Principle | How |
|---|---|
| Alerts are memory | The alert is a frame; the UI is a view; retrieval can surface it. |
| Scheduled tasks are memory | Same pattern, applied to the agent's channel to the user. |
| Lean on the model | The agent decides when to alert and writes its own resolution prompt; the backstop only guarantees the close. |
| No templated responses | Alert content is model-written, not a fixed string per event kind. |
| Clean ship | The `alerts` table is removed; mechanism notices stop being alerts. |
| Stability | One regression test per behaviour, including the never-executed `task_alert` path. |
| Safety & Privacy | Alerts are local memory. A conflict alert is the agent *asking* before searching — it never sends data on its own. |

## Rollback

Phase 1 exports the table then removes it — restore by re-running the export import if
needed. Phases 2–4 are additive (a conversation seed, UI, filtering) and revert with
`git revert`. The existing `AlertsPanel` stays functional until Phase 3 replaces its data
source, so there is no window where the bell shows nothing.
