---
date: 2026-10-15
status: active
estimated_hours: 16
---

# Tasks — a memory-driven daily-task UI

> **The tasks are already memory.** A task is a `scheduled_task` frame whose slots
> (`prompt`, `frequency`, `enabled`, `next_run`, `last_run`, `last_result_summary`,
> `last_output_episode_id`) are the authoritative store. Today **chat is the only
> interface that writes them**. This plan adds a page over that memory, plus the API a
> page needs. It does not add a second model of a task, a tasks table, or a scheduler
> concept — the UI is a view.

## Objective

1. **List** every task the user owns, with its real state (enabled/paused, last run,
   next run, frequency).
2. **Create** a task (name + prompt) and **delete** one.
3. **Edit the `prompt` slot** — and nothing else. See below; this is the whole write
   surface of the page.
4. Show **what a task has been learning** — the facts its runs actually wrote.
5. Roll the TopBar's right-side page links into a **menu**, and add **Tasks** to it.

## The write surface: two fields, two meanings

**The page edits `prompt` and toggles `enabled`. Everything else is read-only.**

| Field | Control | It is… |
|---|---|---|
| `prompt` | the editor, with Save | an **edit** — the script the run executes |
| `enabled` | a pause/resume button | a **state action** — is this task on? |
| `name` | read-only | the frame's identity (UNIQUE; other memory points at it) |
| `frequency` | read-only | `daily` vs `once` — a scheduling decision |
| `description` | read-only | chat's extractor produces it; the UI has nothing to base one on |

**One writer per field** still holds: chat writes all five, and the page writes two of
them through the same store method chat uses. `name` and `frequency` stay conversational
because each is a *decision about identity or scheduling* rather than content — and
`frequency` has a one-way door in it (`once` disables itself after firing).

Consequences, stated so they are not surprises:

- **Create is daily-only.** `frequency` is not offered, so a UI-created task is a standing
  daily task; a `once` task is still created in chat.
- **A UI-created task has no `description`.** The list shows the prompt's first line.
- **Pausing clears `next_run`; resuming sets it to the next daily tick.** That is already
  what `upsert_scheduled_task` does — `next_run` is computed when `enabled` and not
  supplied — so the page adds no scheduling logic, and a resume does not immediately fire
  the tick it missed.

## What exists today

| Thing | Where |
|---|---|
| Task frame + slots, authoritative reads | `memory/store.py` — `upsert_scheduled_task` (:2541), `get_scheduled_tasks` (:2664), `update_scheduled_task_run` (:2807), `delete_scheduled_task` (:2897), `upsert_scheduled_task_slot` (:3010) |
| The one clock, `daily` / `once` | `scheduler/schedule.py` — `next_daily_run`, `format_next_run` |
| The run | `scheduler/runner.py` `execute_and_record_task` (:179) — shared by the scheduler and the manual run-now endpoint. Runs the full cognitive loop, writes `last_run`/`last_result_summary`, associates `task --ran_in--> daily_run` (:226), and sets `last_output_episode_id` (:231). `_execute_task` (:316) wraps it |
| Chat create/update/delete/list/pause | `pipeline/orchestrator.py` `_handle_scheduled_task` (:1055) + `_scheduled_task_management_response` (:1129) |
| HTTP | `main.py` — `GET /tasks` (:1972), `DELETE /tasks/{id}` (:2000), `GET /tasks/{id}/result` (:2010), `POST /tasks/run-due` (:2026), `POST /tasks/run-now/{name}` (:2078) |
| A run's output | `episodes.frame_ids` (the frames the run touched) — `store.py:1177`, `get_episodes_for_session` (:2011) |
| Tests | `tests/test_daily_schedule.py` (25 tests: creation, run-now, the daily-run frame, alerts, rescheduling) |

## The six gaps this closes

These are the reason a page is worth building, and five of them are defects:

1. **No create and no update endpoint.** `POST /tasks` and `PATCH /tasks/{id}` do not
   exist. Chat is the only way to make or change a task, so "create, update, delete"
   is currently one-third true.
2. **`DELETE /tasks/{task_id}` does not take `user_id` and does not check ownership**
   (`main.py:2000`). Any caller can soft-delete *any* task frame, including another
   household member's — and system frames. This is the hardcoded-user-id rule's
   sibling: the request never says who it is for.
3. **`GET /tasks/{task_id}/result` searches every task including system ones**
   (`main.py:2010`, `get_scheduled_tasks(include_system=True)`), so it will return the
   result of a task the caller does not own. Same class as (2).
4. **Three of the five endpoints have no consumer at all.** `GET /tasks`,
   `DELETE /tasks/{id}` and `GET /tasks/{id}/result` are called by nothing — not the
   frontend, not `cli/app.py`, not `backend/static/`. Only `/tasks/run-due` is reached,
   by one test. The API was written for a UI that was never built.
5. **Delete-then-recreate the same name raises `IntegrityError`.** Measured against a
   scratch store, not reasoned about:

   ```
   delete->recreate: RAISED IntegrityError UNIQUE constraint failed: frames.name
   ```

   `delete_scheduled_task` soft-deletes (the name row survives, `frames.name` is
   `UNIQUE`), and `upsert_scheduled_task`'s live lookup filters `deleted_at IS NULL`, so
   it finds nothing and INSERTs into a taken name. **This is the first thing a user will
   do** on a page with a Create button and a Delete button.
6. **Task names are global, so one account's create rewrites another's task.** Also
   measured:

   ```
   bob's task list after creating 'morning': []
   alice's task after bob created the same name: [('morning', "BOB'S")]
   ```

   `upsert_scheduled_task`'s existing-lookup is `WHERE name = ? AND type =
   'scheduled_task' AND deleted_at IS NULL` — **no `owner_user_id`** — and `frames.name`
   is globally `UNIQUE`. Bob's create updated Alice's task, changed her prompt, and left
   Bob with nothing.

Gaps 5 and 6 are pre-existing, but a create/delete UI is what makes them reachable.

A seventh, found while implementing: **pausing a task left its `next_run` slot set.** The
column was cleared but the slot is what reads come from, so the API would report "next
run: tomorrow" for a task that will not run. Caught by a test written from this plan's
own claim that pausing clears `next_run` — the claim was right and the code was not.

And an eighth, found by using the page: **`GET /tasks` and the `/tasks` page route were
the same path.** The backend serves the SPA for any path it has no exact route for, so
the API handler won and the page returned `422 {"detail": …}`. Fixed by moving every
route under `/tasks/` (`/tasks/list`, `/tasks/create`). The same investigation found the
dev `Caddyfile`'s `/files*` and `/brain*` matchers were serving the **built** bundle for
those two pages instead of Vite — the page worked, HMR did not. The rule is now in
`docs/DEPLOYMENT_MODES.md`.

And one piece of data that is **written but never read**: `last_output_episode_id`
(`runner.py:231`). It is the exact hook objective 4 needs.

## Architecture

> **The page is a view over the task frames; the store methods chat already uses are
> the only writers.**

```
Tasks page ──► /tasks API ──► MemoryStore.upsert_scheduled_task / delete / update_run
   ▲                                        ▲
   │                                        │
   └── reads ── get_scheduled_tasks ─────────┘        (chat writes through the same methods)
```

- **No new table, no new frame type, no tasks-in-a-json-file.** A task is memory; the
  daily-run event frame, its `ran_in` association, and the output episode's `frame_ids`
  are already there.
- **The page writes through the same store methods the chat path uses.** The maps work
  taught this the expensive way: the map page was the only writer that skipped
  `apply_file_to_memory`, and the user's own file was invisible in the Files view.
- **Ownership is explicit on every endpoint** (`assistant/AGENTS.md`, "Accounts"). A task
  belongs to an `owner_user_id`; a request names the account and the store filters by it.
  A task you do not own is a 404, not a silent success.
- **`schedule_cron` is a misnomer** — it holds `daily` or `once`, not a cron expression.
  The response keeps the field name for compatibility; new UI labels say **Frequency**,
  and the plan does not spread the old name further.

### The task name (gaps 5 and 6)

A task's name is its identity: `frames.name` is `UNIQUE` and `upsert_scheduled_task` finds
an existing task *by name*. Both bugs come from that name being global while tasks are
per-owner.

Two ways out; the recommendation is the second:

- **Minimal.** Scope the live lookup by owner (`AND owner_user_id = ?`) and catch
  `IntegrityError` on insert, falling back to `f"{name}__u{owner_user_id}"` — the escape
  hatch `create_frame` already uses (`store.py:421`) for exactly this collision. Small and
  no migration, but a stored name may then read `morning__u2`, so the UI needs the logical
  name from somewhere other than the frame row.
- **Recommended.** Give the frame an owner-scoped stored name from the start
  (`{name}__u{owner}`) and keep the **logical** name in a `task_name` slot — which is how
  file frames already work (`file_map.geojson__u1`, with `file_name` in a slot, written by
  `apply_file_to_memory`). Two accounts can then each own a "briefing", a tombstoned name
  never collides, and the API keeps returning the logical name. It needs a small migration
  for existing rows; `migrate_scheduled_tasks_to_slots` (:2940) is the precedent and the
  natural place for it.

Either way, the name a task is *created* with and the name a user *sees* must be the same
string — chat matches a task by name ("run my briefing now"), so a mismatch between the
stored name and the logical one breaks run-now.

## The API

> **Every task route lives under `/tasks/`.** `/tasks` is the page route, and the backend
> serves the SPA for any path it has no *exact* route for — so a handler at the bare path
> wins and the page returns JSON. That is exactly what `GET /tasks` did on the first
> build. See `docs/DEPLOYMENT_MODES.md`, "Page routes and API paths".

| Method | Path | Change |
|---|---|---|
| `GET` | `/tasks/list?user_id=` | `user_id` **required**. System tasks are not returned. Today `user_id` omitted means "include system tasks" — an admin view reachable by accident |
| `POST` | `/tasks/create?user_id=` | **New.** Create. Body `{name, prompt}`. `frequency` is fixed at `daily`; `description` is empty |
| `PATCH` | `/tasks/{id}?user_id=` | **New.** Body `{prompt?}` or `{enabled?}` — the page's two writable fields and nothing else. 404 when the id is not the caller's |
| `DELETE` | `/tasks/{id}?user_id=` | **Fix.** Verify ownership before the soft delete |
| `GET` | `/tasks/{id}/result?user_id=` | **Fix.** Ownership-checked; drop `include_system=True` |
| `GET` | `/tasks/{id}/runs?user_id=&limit=` | **New.** The learning view (below) |
| `POST` | `/tasks/run-now/{name}?user_id=` | Unchanged (already takes `user_id`) |
| `POST` | `/tasks/run-due` | Unchanged (scheduler-facing) |

`PATCH` accepting exactly two named fields is the contract doing its job: the body model
is `{prompt: str | None, enabled: bool | None}` with `extra="forbid"` and a validator
requiring at least one, so a client that tries to change `frequency` gets a 422 rather
than a silent partial update. If the UI ever needs a third field, that is a deliberate
widening with a test, not a drift.

Validation at the boundary: `prompt` non-empty (a blank prompt is a task that runs and
does nothing), `name` non-empty, and an empty `PATCH` body is a 422. `frequency` is not
accepted from the client at all.

### The learning read

`GET /tasks/{id}/runs` returns, newest first:

```json
{ "runs": [
  { "date": "2026-10-14", "daily_run_frame_id": 412, "status": "completed",
    "episode_id": 9182, "summary": "…", "learned": [
      { "frame": "ai_news_briefing", "type": "topic",
        "slots": [{"key": "latest_release", "value": "…"}] } ] } ] }
```

- **Runs** come from the `ran_in` associations (`runner.py:226`), which already link the
  task to each `daily_run_YYYY_MM_DD` frame; that frame carries `date` and `status`.
- **`learned`** comes from the run's output episode: `episodes.frame_ids` is the set of
  frames the run touched, and `get_episodes_for_frames` (:2088) already exists. Filter
  to frames the run *wrote* to (slots whose `source_episode_id` is that episode) so the
  panel shows what changed, not what was merely read.
- **One new store method**: a public `get_episode(episode_id)`. `_get_episode_row`
  (:1174) is private and `get_last_assistant_episode` (:3023) only finds the latest.

**This is the piece with a real unknown**, so it gets a measurement first (Phase 0).

## The frontend

New files, following the Files page exactly (`FilesPage.tsx` + `styles/files.css` is the
established shape for a list-and-detail page):

```
frontend/src/components/tasks/TasksPage.tsx      # the page: list + detail panel
frontend/src/components/tasks/TaskForm.tsx       # detail: prompt edit, pause/resume, delete
frontend/src/components/tasks/TaskRuns.tsx       # what it has been learning (Phase 3)
frontend/src/components/tasks/TasksPage.test.tsx
frontend/src/styles/tasks.css                    # imported from styles/index.css (#12)
frontend/src/types/task.ts                       # Task, TaskChange
frontend/src/utils/taskLib.ts                    # formatStamp, taskStateLabel, taskNextRunLabel, taskSubtitle
```

- **Route** `/tasks` in `routes.tsx` (static import, like `/files`).
- **API client** in `services/api.ts`: `getTasks`, `createTask`, `updateTaskPrompt`,
  `deleteTask`, `getTaskRuns` — all through `actingUser()`, so the account travels with
  the request and no call site carries an id.
- **State**: local to the page. It is a CRUD list, not shared app state — `state/files.ts`
  is shared only because the chat links filenames to it, and nothing links to a task.
- **Detail panel**: the task's identity read-only (name, frequency, last run, next run)
  above one editable field — the prompt — with Save and Delete, and a pause/resume
  button. The read-only fields are shown as text, not disabled inputs, so the page does
  not imply an edit that is not offered; `enabled` is a button because it is an action,
  and the row shows the resulting state.
- **No new CSS system**: `tasks.css` reuses the `files.css` layout classes where the shape
  is identical and defines only what is genuinely new. `designSystem.test.ts` fails a bare
  class defined in two stylesheets, so shared classes are *used*, not redefined.
- **Feedback**: a row shows a pending state while a save is in flight; a delete asks
  first; a failed save keeps the edit on screen and says so (the map page's pattern).

## The menu

Today the TopBar's right side is five items plus the trash can (`TopBar.tsx:361-403`):
**Files**, **Maps** (gated), **Brain** as links; **Trace** and **Settings** as panel
toggles. Adding Tasks makes six — and the row does **not** wrap: `header` and
`.header-right` are single flex lines (`base.css:16`, `components.css:108`) with no
`flex-wrap`, so a sixth item squashes the row rather than flowing onto a second line.
That is the actual motivation: the row has run out of width, not of lines.

> **Revised while implementing:** the menu took the toggles and the trash too, so the
> row is down to **Alerts, Let's talk, Pages**. The original plan kept Trace and
> Settings out on the grounds that they are stateful toggles; that was overruled, and
> `Trace` renders its own `is-active` state so nothing is lost. `TrashCan` became a
> controlled modal (`isOpen`/`onClose`) because its trigger is a menu item while the
> modal must outlive the menu — the panel unmounts on close and `Modal` is not a portal.

The menu is one button, generated from a list, containing **Tasks · Files · Maps ·
Brain | Trace · Settings · Trash**.

Responsive: the menu button is always present (one code path, and it is what "roll up"
asks for); the icons stay beside it for Trace/Settings/Trash. The menu closes on Escape,
on outside click, and on navigation — the conversation dropdown already does the first
two, and that behaviour should be lifted into a shared hook rather than copied.

## Phases

**Phase 0 — measure before building the learning view (30 min, read-only).**
The one belief worth checking is *"a task's runs leave a readable trail of what they
learned."* Run `assistant/experiments/preflight.py` against a brain copy and count: how
many `scheduled_task` frames exist, how many have `ran_in` associations, how many of those
runs have an output episode, and how many of those episodes have non-empty `frame_ids`.
**Falsification:** if fewer than ~half the runs have an episode with `frame_ids`, the
`learned` list is mostly empty and Phase 3 becomes "show the run summaries" instead. Write
`plan.md` before looking, `result.md` after.

**Phase 1 — the API (4.5h). DONE.** `user_id` required on `GET /tasks`; ownership on
`DELETE` and `/result`; `POST /tasks`; `PATCH /tasks/{id}` (prompt or enabled,
`extra="forbid"`, at least one required). `GET /tasks/{id}/runs` **moved to Phase 3** —
it is the learning read, and building it before Phase 0 measures the trail would be
building on the unknown.

**Phase 1a — the task name (3h). DONE.** Gaps 5 and 6, which a Create button makes
reachable: owner-scope the lookup, and make the stored name owner-scoped with the logical
name in a `task_name` slot. Failing tests first; both reproduced the measured errors.
**Do this before Phase 2** — building a Create button on top of a name that cannot be
recreated is building on a known break.

**Phase 2 — the Tasks page (4h). DONE.** List, create, edit the prompt, pause/resume,
delete. No learning panel yet — the page must be useful on its own.

Two deviations from the file list below, both deliberate: **`TaskList.tsx` was not
needed** — the list is ~30 lines of JSX inside the page, and a component whose only
caller is its parent is indirection, not modularity. And the delete confirmation is
`window.confirm`, not the house `Modal`: it is one call and needs no state. If the page
grows a second destructive action, both should move to `Modal` together.

**Phase 3 — the learning panel (3h).** The `TaskRuns` view, shaped by Phase 0's result,
plus `GET /tasks/{id}/runs` (moved here from Phase 1 — it *is* the learning read) and the
public `get_episode` it needs.

**Phase 4 — the menu (2h). DONE.** The dropdown, Tasks added, Trace/Settings left in
place, `TopBar.test.tsx` updated (two order tests and the link test moved into the
menu). Verified served on dev; still to check on prod (8444) at a narrow width.

## Test strategy

**Backend** (`tests/test_daily_schedule.py`, plus a new `tests/test_tasks_api.py`):
- create → list → the task is there with `frequency=daily`, the prompt stored, `next_run`
  set; a second create with the same name updates rather than duplicating (the store's
  existing behaviour)
- **`PATCH` with `{"prompt": "…", "frequency": "once"}` is a 422** — the two-field contract
- pause sets `enabled=false` **and clears `next_run`**; resume sets `enabled=true` and
  `next_run` to the next daily tick, so a resume does not fire the tick it missed
- an empty `PATCH` body is a 422
- `PATCH` to a task you do not own is a **404**, and its prompt is unchanged
- delete your own task; delete someone else's is a **404** and the frame survives
- `GET /tasks` without `user_id` is a **422** (the new rule)
- `/tasks/{id}/result` for another user's task is a 404, not a leak
- **Regression, named:** *"a task belonging to another account cannot be read, changed or
  deleted"* — the defect (2) and (3) class
- **Regression, named:** *"delete a task, create one with the same name, and it works"* —
  defect (5), measured above
- **Regression, named:** *"two accounts can each own a task of the same name"* — defect
  (6), measured above

**Frontend** (`TasksPage.test.tsx`, `TopBar.test.tsx`):
- the list renders tasks with their real state; an empty list says so
- create posts once and the new task appears without a reload
- editing sends exactly `{prompt}` and the toggle sends exactly `{enabled}` (the tests
  assert both request bodies, so a third field cannot be added silently)
- a failed save keeps the edit on screen (the map page's regression, reused)
- delete asks before it deletes
- the menu opens, closes on Escape/outside-click, and contains Tasks

**Critical path:** `test_daily_schedule.py` + `test_review_fixes.py` must pass before this
is considered done (AGENTS.md), plus the full suite and `npm run check`.

## Acceptance criteria

- [ ] Every task the user owns is listed with frequency, state, last run and next run
- [ ] Create and delete work from the page; the prompt is editable and pause/resume
      toggles state — no other field is writable
- [ ] A change survives a reload — it is memory, not local state
- [ ] A task created in the UI is visible to chat ("list my scheduled tasks") and vice
      versa — one store, two interfaces
- [ ] No endpoint returns or mutates a task the caller does not own
- [ ] Deleting a task and creating one with the same name works, and two accounts can each
      own a task of the same name
- [ ] `GET /tasks` requires `user_id`
- [ ] The learning panel shows, per run, the frames the run wrote to — or the plan is
      revised to show run summaries, if Phase 0 says the trail is thin
- [ ] Tasks is reachable from the TopBar menu on both dev and prod, at 1280px and 700px

## Constraints

- **Security/privacy:** all local; no new network calls. Prompts are user content and are
  stored exactly as chat stores them.
- **Performance:** the list is one store read; the learning read is per-task and only on
  demand (not on page load) — a task with 200 runs must not make the page slow.
- **Compatibility:** `schedule_cron` stays in the response for the CLI and tests; the UI
  calls it Frequency. No DB migration — slots are already the authoritative store and
  `migrate_scheduled_tasks_to_slots` (:2940) already handles legacy rows.
- **No new dependency.**

## Rollback

Additive and separable. Phases 1–3 add endpoints, a route and a page; deleting the route
and the menu entry returns the app to today's behaviour, with the endpoints left unused
(as three already are). No data migration to reverse: the UI writes the same slots chat
writes. Revert is `git revert` of the phase commits; the brain is untouched.

## Fact-check (RUNBOOK §6)

Every `file:line` in this plan was re-read against the tree. Three claims were wrong:

| Claim | Reality |
|---|---|
| The run recorder is `_execute_task` (:185) | It is **`execute_and_record_task` (:179)** — the function whose own docstring says "shared by scheduler runner and manual API endpoint". `_execute_task` (:316) wraps it, so the old reference pointed at the wrong function |
| `_get_episode_row` is at :1170 | :1174 |
| "the row already wraps on a narrow window" | It does **not**. `header` (`base.css:16`) and `.header-right` (`components.css:108`) are single flex lines with no `flex-wrap`; the `flex-wrap` in `layout.css:128` belongs to `#input-row .file-chips`. A sixth item squashes the row |

Verified and unchanged: `store.py` :2541 / :2664 / :2807 / :2897 / :2940 / :3010 / :3023 /
:2011 / :2088, `store.py:421` (`__u`), `runner.py:226` / `:231`, `orchestrator.py:1055` /
`:1129`, `main.py:1972` / :2000 / :2010 / :2026 / :2078, `TopBar.tsx:361-403`,
`TopBar.module.css:9` (the dropdown pattern this reuses), `schedule.py:38` / :49,
`test_daily_schedule.py` = 25 tests, `experiments/preflight.py` exists, and
`Episode.frame_ids` is populated by `update_episode_frame_ids` (:2079) *after extraction*
— so it is the set of frames a turn touched, which is what the learning view needs.

Ordering note: **Phase 0 only gates Phase 3.** It is a measurement of the learning trail,
so it can run at any point before Phase 3 without holding up 1, 1a or 2.

## Design-principles check (RUNBOOK §7)

- **Clean ship** — the plan *removes* three unreachable endpoints' worth of dead surface
  by giving them a consumer, and wires `last_output_episode_id`, written today and read by
  nothing. `schedule_cron`'s name is noted, not propagated.
- **Speed-first** — the list is one read; the runs view is on demand. No model call on
  page load.
- **Visual feedback** — pending/saved/failed states on save; delete confirms; the menu uses
  the existing 150–300ms transitions.
- **Stability** — regression tests for the ownership class (a task you do not own), for the
  one-field PATCH contract, and for the failed-save path, plus the required
  `test_daily_schedule.py`.
- **Safety & privacy** — no new external calls; ownership enforced on every endpoint.
- **Model-first / no templated responses** — the page generates no text the model should
  own. `last_result_summary` is the model's own output, shown verbatim; nothing here writes
  an acknowledgment.

## Open decisions

1. **Does the learning panel sit in the detail panel, or behind a tab?** Recommended: in
   the detail panel, collapsed by default and loaded on open.

Settled during planning: **pause/resume is in the page** (as a state action, not an edit —
see the write surface above), and **the menu holds every control** — pages, toggles and
trash — leaving the row as Alerts, Let's talk, Pages.
