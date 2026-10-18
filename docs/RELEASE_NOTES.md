# MyDigitalAssistant.ai — release notes

## v0.11.21-alpha

**The agent now finishes what it starts — and why it did not was measured, twice wrongly, and
then found.** A scheduled task that asks for a file to be written was finishing its report and
leaving the file alone. Three defects in the tool loop were fixed in sequence, and an
experiment that runs the task end to end says which of them mattered.

- **The completion check is structural.** It used to be a model judgement (`outstanding`,
  `nimble`) asking a small model whether the calls had done the job. A `read_file` satisfied
  it: in the first measured run all seven failures had read the CSV and stopped, and the check
  reported nothing wrong. It is a rule about tool names now: `needs_action` decides the **kind** of act the request needs (`change` / `lookup`), and
  the code decides whether a tool of that kind ran. A lookup cannot satisfy a change.
- **One nudge per failure, not per turn.** There are two distinct failures — the model
  narrated and called nothing, and the model called something that did not perform the action
  — and a single shared allowance meant the second could never fire. That is the *common*
  sequence, so the completion nudge was dead for the case it exists for.
- **The ceiling spends the nudge instead of reporting.** Running out of rounds fell through to
  a wrap-up that disables tools (`tool_choice="none"`) and asks the model what blocked it, so
  a turn could produce a *reason* but never act — and the completion nudge, which produced
  every write in the measured runs, was never reached. The ceiling now grants a bounded acting
  round first; `grace_rounds_left` is the loop's termination argument.
- **A task cannot start a task.** `run_scheduled_task` was callable from inside a scheduled
  run, and the scheduler's firing path has no timeout, so a task naming itself would nest
  whole runs rather than fail one turn. Refused now, on the scheduled session id. The
  chat-side "run my briefing now" route is unaffected.
- **`tool_loop_exit: reason=…`** is logged at INFO, so why a turn stopped is observed rather
  than inferred.
- **Measured, not asserted.** `experiments/ship_tracker_efficacy/` runs the user's real prompt
  K=10 against a brain copy: `no_attempt` 3/10 → **0/10**, `wrote_complete` 7/10 → **9/10**,
  H1 met. `7 → 9` is *not* resolvable at K=10; what the run establishes is `no_attempt` → 0
  and that no trial exits the ceiling with its action unperformed. **Two runs are recorded as
  invalid**, for two different harness faults: an early attempt died on a hardcoded `base_url`
  default before the model saw the prompt, and runs 2–3 measured an agent whose `web_search`
  was never wired (`init_store(search_tool=None)`) — which read exactly like a model refusing
  to act. The first run whose results were kept is not in that class: its failures never
  searched.
- **UI:** the page menu is on every page, with Chat leading off the chat page; the map page
  picks which sandbox GeoJSON file it is working on and treats a bare `Feature` as a one-point
  map; `.geojson` can be written and previewed; the "For You" bell is absent when nothing is
  waiting; and the account is resolved before any route renders.

**Known, measured, and not yet fixed** — recorded rather than left to be rediscovered:

- `write_file` refuses to replace an existing file without `overwrite=True`, while a task
  prompt typically says "update the file". It changed an outcome in run 5, and it will fire on
  the ship-tracker task every morning.
- The model re-writes a file it has already written correctly — up to five times in one run —
  spending the rounds the ceiling then cuts off.
- The `needs_action` scope was measured against the earlier **two-way** question. The question
  is three-way now, with the threshold applied per kind, so those figures owe a
  re-measurement. Every place that cites them says so.

## v0.11.20-alpha

**A Tasks page — list, create, edit the prompt, pause/resume and run now, over the
`scheduled_task` frames that were already memory — plus a routing collision that had been
serving the *built* bundle for two pages in dev.**

- **The Tasks page.** `/tasks` is a view over memory, not a second model of a task: it
  reads the same `scheduled_task` frames chat writes, through the same store method.
  Create, delete, and exactly two writable fields — `prompt` (an edit) and `enabled` (a
  state action). `name`, `frequency` and `description` stay conversational, and the server
  refuses anything else (`extra="forbid"`), so a third field cannot drift in unnoticed. A
  test asserts the exact request bodies.
- **Run now.** Runs the prompt through the same path the scheduler uses and shows the new
  result. Called *Run now* rather than *test run* because it is one: the loop writes an
  episode, extraction writes frames, `last_run` moves, a daily task is rescheduled to
  tomorrow, and a `once` task switches itself off — so the button confirms first and says
  which applies. Progress is an elapsed-seconds counter, because a run is one blocking
  request and this path exposes no stage events; a moving number is what separates
  "working" from "hung". There is deliberately no dry-run endpoint — a second path would
  drift from what the 9am run actually does.
- **A task name is per-owner, and can be recreated.** Two defects, both measured against a
  scratch store rather than reasoned about:

  ```
  delete->recreate: RAISED IntegrityError UNIQUE constraint failed: frames.name
  alice's task after bob created the same name: [('morning', "BOB'S")]
  ```

  `frames.name` is globally UNIQUE, but a task belongs to an owner. A task is now addressed
  by `(name, owner_user_id)`, stored under `{name}__u{owner}` when the plain name is taken,
  with the name the user gave it in a `task_name` slot so the handle stays internal. Both
  are the first thing a Create button reaches.
- **Ownership on every task endpoint.** `GET /tasks` defaulted `user_id` to `None`, which
  the store reads as "no owner filter" — omitting the parameter returned **every** task in
  the install, system ones included. `DELETE /tasks/{id}` took no `user_id` at all, so any
  caller could soft-delete any task frame. `GET /tasks/{id}/result` scanned with
  `include_system=True`; it is **removed** rather than fixed, because the list carries
  `last_result_summary` on every row and the review found no consumer left. A task you do
  not own is a **404**, not a 403 — the id is not a secret, and a 403 would confirm it.
- **A page route and an API path must not share a path.** `/tasks` served the API's 422
  instead of the page: the backend serves the SPA for any path it has no *exact* route for,
  so a handler at the bare page path wins. Underneath were two more problems — the dev
  `Caddyfile`'s `/files*` and `/brain*` wildcards were swallowing those two **pages** and
  serving the **built** bundle from the backend rather than Vite (the page worked, HMR did
  not, and it could be arbitrarily stale), and `vite.config.ts` prefix-matches its proxy
  keys, so a bare `'/files'` forwarded the page before Vite ever served it. Three matchers
  have to agree; that is now in `docs/DEPLOYMENT_MODES.md` with both failure modes and a
  one-line check.
- **One menu for every right-side control.** Tasks, Files, Maps, Brain, Trace, Settings and
  Trash in one dropdown, leaving the row as Alerts · Let's talk · Pages. The row does not
  wrap — `header-right` is a single flex line — so adding Tasks squashed it. Destinations
  are links and the toggles are buttons, styled identically by one rule that resets the UA
  button box: nothing in the stylesheet styles a bare `button`, which is why three of them
  were sitting there as white chips.
- **One markdown renderer.** `marked.use` lived inside `MessageContent.tsx` — global state
  configured in a component, so it applied only when that component happened to be loaded;
  the Files viewer and the Tasks page parsed markdown with no config at all. A test written
  for the new `utils/markdown.ts` found that the `target="_blank"` in that config **never
  took effect** (DOMPurify strips it), which is fine: `ExternalLinkGuard` opens off-origin
  links in a new tab *after confirming*, and exists precisely because `innerHTML` cannot
  carry a per-link handler.
- **From the review.** The task list was N+1 — a slot query per task, on the new page's hot
  path; `get_slots_for_frames` already existed for exactly that and now backs both
  `get_scheduled_tasks` and `get_due_scheduled_tasks`. One endpoint was removed as dead. The
  routing rule moved into `AGENTS.md`'s Do/Don't, so it is found before the next page is
  added rather than after.
- **Not in this release.** The Tasks page's *learning* panel — what a task's runs have been
  writing — is planned and not built. It waits on a measurement
  (`plans/2026-10-15-daily-tasks-ui.md`, Phase 0): how many runs leave an output episode
  with `frame_ids`, which is what the panel would read. Building the view before measuring
  the trail is the thing that plan exists to avoid.

## v0.11.19-alpha

**A map page — a GeoJSON file in the sandbox the agent reads — and four bugs the map
work flushed out, three of which were invisible in production until something asked
the console a question.**

- **Maps.** `/maps` is MapLibre GL on an OpenFreeMap basemap over an ordinary sandbox
  file, `map.geojson` at the sandbox root. The page is the only writer (`PUT /map` with
  the whole FeatureCollection); the agent reads it with `read_file`, and
  `glob("*.geojson")` finds it. **One `Map` instance for the page's lifetime** — a
  WebGL context is scarce (~8–16/page) and permanently held — so a style switch calls
  `setStyle` rather than remounting. Off by default; on in dev, off in prod. See
  `docs/MAPS.md`.
- **Click the map to add a point — there is no mode.** The `＋ Add point` toggle made
  the primary interaction two-step, and it existed for exactly one reason: a bare click
  would drop a stray point while panning. MapLibre already handles that (`dragEnd`
  calls `DOM.suppressClick()`), so the click carries the interaction now: bare basemap
  starts a new point, a saved point selects it, a cluster eases in to split. The pending
  point is drawn from its own source — visible immediately, and nothing reaches the file
  until you confirm.
- **`debug()` had never logged anything.** Vite injects `import.meta.env` into a module
  only where it sees the literal; `logger.ts` read it through a variable
  (`const meta = import.meta; meta.env?.DEV`), so it was `undefined` there and
  `isDev()` was always false. **Every `debug()` call in the app was a silent no-op** —
  which is why a blank map page produced nothing at all in the console. Fixed, with a
  regression test.
- **The map canvas lost a CSS tie and collapsed to zero height.** MapLibre stamps
  `maplibregl-map` onto the same element as `.map-canvas` and injects its stylesheet
  *after* ours, so `.maplibregl-map{position:relative}` beat
  `.map-canvas{position:absolute}` at equal specificity. The container left the flow,
  `clientHeight` became 0, and MapLibre took its own fallback — `clientHeight || 300` —
  into a box that `overflow:hidden` then clipped to nothing: a blank page, no error, and
  no surface to click. jsdom computes no layout, which is why every component test
  passed; the specificity is now pinned by a stylesheet test.
- **The labels had no font.** `text-font` was unset, so MapLibre used `Open Sans
  Regular` — which OpenFreeMap answers **404**. A glyph that failed to load has zero
  advance width, so the label still drew, every character on top of the last: unreadable
  overlap rather than a missing-font error. The font now travels in
  `MAP_STYLES[].label_font` beside the colours (the module's own rule is that the client
  never sniffs the style's layers), and the labels are larger with a `text-max-width`, so
  a long note wraps instead of running one line across the map.
- **The map was on disk but not in the Files view.** `GET /files/list` is a view over
  file *frames*, and `PUT /map` wrote the bytes and stopped. Every other writer of a
  sandbox file — the upload endpoint, `write_file`, `edit_file` — funnels through
  `apply_file_to_memory`, the documented "single path for upload and write"; the map page
  skipped it, so the user's own content was readable by the agent and invisible to the
  user. Saving now goes through that same step, wrapped like `edit_file`'s refresh: the
  bytes are the source of truth, so a memory failure must not turn a save that happened
  into a 500 the UI reports as "could not save".
- **A user id is data, never a literal.** Nineteen places defaulted the account to
  `1` — a parameter default, a fallback when a value would not parse, a stand-in for a
  scheduled task with no owner — each assuming an account with that id existed *and* that
  it was the one calling. The backend now takes `user_id` as a **required** parameter;
  the frontend resolves the account once (`services/actingUser`) and the API layer
  attaches it, so no call site carries a literal; and a tool that cannot parse the
  caller's id writes its frame **unowned** rather than inventing an owner. The rule is
  recorded in `assistant/AGENTS.md` and enforced by `test_user_id_not_hardcoded.py`.
- **Two more literals, found by writing that guard.** `GET /search` passed `user_id=0`
  with the comment "not user-specific in this endpoint" — but `0` is not `None`, so the
  store filtered to `owner_user_id = 0 OR owner_user_id IS NULL` and returned **only
  unowned frames**. On the dev brain that hid **311 of 4669 frames** — every scheduled
  task, daily-run event and conversation summary the user owned — behind a
  healthy-looking page of ten results. `Alert.user_id` used a `0` sentinel too; it is
  `int | None` now.
- **The plan-citation detector had been red since the maps plan shipped.** It scans
  every durable file for `plans/<name>.md` citations — including the file that defines
  its own fixtures. When that plan was deleted, the detector reported *itself*. The
  fixture is built from parts now, so the check still works and no longer self-trips.
- **Considered, not built.** Local/geo recall — answering "what's near me" without the
  user naming a place — is recorded as declined in `docs/SEARCH.md` §3 ("Local recall
  (geo) — deliberately not built"): it needs the user's coordinates sent to Brave, which
  retains what it is sent for up to 90 days. The `brave_context_budget` plan was deleted
  with its reasoning folded into the same doc (§5), alongside the `freshness` parameter
  and the per-result text budget.

## v0.11.18-alpha

**Brave search runs on the LLM Context endpoint now — one request per turn, with
page dates, and sources that match the answer — and the test suite can no longer
hang.**

- **Brave's LLM Context is the default.** `BRAVE_LLM_CONTEXT=true` makes
  `BraveBackend.search()` use `/res/v1/llm/context` (pre-extracted,
  relevance-ranked content chunks per URL — the channel Brave's docs recommend for
  agents) instead of `/web/search`, and the orchestrator skips its own page-fetch.
  `snippet` = the first chunk, the rest ride in `extra_snippets`, so extraction
  needed no change. Two accepted trades (`docs/SEARCH.md`): **videos are gone** (no
  `videos` section) and result images are OG-class `sources[].thumbnail`, not the
  500px image index. `BRAVE_LLM_CONTEXT=false` restores `/web/search`.
- **One Brave request per search turn.** The LLM Context response already carries
  the imagery, so the image index is not called on that path — roughly **1,000
  turns/month** on the free allowance, up from ~500. The `/web/search` fallback
  still runs web + images.
- **Page dates.** `sources[].age` rides on the result and into both the prompt
  (`(2025-11-10)`) and the extraction input (`Published: 2025-11-10`), with a
  prompt rule to prefer recent results on time-sensitive queries — no extra
  request.
- **Sources reflect the turn.** The `**Sources:**` footer cites the pages the
  extraction drew facts from, not every result, and near-duplicate URLs collapse
  (`normalize_url`). No frontend change was needed.
- **The suite can no longer hang.** A test driving one `MemoryStore` through four
  `asyncio.run()` loops deadlocked an aiosqlite worker thread in sqlcipher and
  stalled the whole run; the file now runs on one loop per test, and a
  `pytest-timeout` (120s) guard turns any future hang into a named failure.
- **Considered, not built.** The two remaining levers — rebalancing the per-result
  text budget and the `freshness` request parameter — are recorded as declined,
  with the reasoning, in `docs/SEARCH.md` §5.

## v0.11.17-alpha

**The file sandbox becomes the host folder — and the brain moves out of its way —
and Brave search asks for the excerpts it was already entitled to.**

- **The file sandbox *is* the host folder now.** `/app/data` is bind-mounted to a
  directory on the host (`~/Documents/Agent/Development` in dev,
  `~/Documents/Agent/Production` in prod), so every file the agent reads or writes
  is openable and editable with
  host tools, and files you drop in are read at sandbox-relative paths
  (`read_file("notes.md")`). This replaces v0.11.16's nested `shared/` subfolder,
  which never exposed the files the agent *already had*. **The brain is deliberately
  kept out of it**: the SQLite DB and its backups live in a volume mounted at
  `/app/brain` (`DATABASE_PATH`), so exposing the sandbox never exposes or
  duplicates the brain — and the DB file itself never moves, only the path it is
  opened through. `SANDBOX_DIR_DEV` / `SANDBOX_DIR_PROD` override one environment;
  `SANDBOX_DIR` overrides both. **Upgrading an existing install:** nothing is lost
  (the DB stays in its volume, now at `/app/brain`), but the user files that used to
  sit beside it do not appear in the sandbox until you copy them out once — see
  `docs/FILES.md`.
- **Brave search asks for `extra_snippets`.** The web request now sets
  `extra_snippets=true` (up to 5 further excerpts per page, **no extra request**),
  and the extras are folded into the *extraction* input, bounded per result
  (`_SEARCH_RESULT_TEXT_CHARS`). `snippet` stays lean, so the prompt and the
  relevance gate are unchanged. Brave's dedicated **LLM Context** endpoint — the
  content channel its docs actually recommend for agents — is pre-registered for a
  measured swap in `assistant/experiments/brave_llm_context/plan.md`.

## v0.11.16-alpha

**The file list stops lying, the compute path stops promising libraries it does not
have, and the fleet settles to what has been measured.**

- **A file edit now moves the file's `updated_at`.** Confirmed on the prod brain:
  `Why Not Release.md` was written at `13:39:45` while its frame still said three days
  earlier — its `file_size` *slot* was current, so the content was saved and only the
  frame **row** was not. `apply_file_to_memory` (the one shared path for upload, write,
  append and edit) upserts slots, and **a slot write does not touch its frame**, so the
  Files list — which sorts on `frames.updated_at` — never moved an edited file.
  `store.touch_frame()` fixes all four callers at once. The same property holds
  brain-wide: 99% of frames carry `updated_at == created_at`.
- **The compute path promised libraries the sandbox does not have.** Its system prompt
  said *"Use numpy, scipy, sympy, pandas, statistics as needed"*; only numpy is
  installed, and numpy 2 removed `np.irr`. **Every** hard-math failure in the probe was
  a `ModuleNotFoundError` — the model was punished for following instructions. With the
  prompt telling the truth the same arm went from 4–8/12 to 8–12/12 **without touching
  the fleet**, and the model comparison became valid for the first time.
- **The dedicated math model is retired — now on evidence.** It *tied* the warm utility
  model on business arithmetic (30/30 each) and is the **worst of three** on hard math
  (8/12, against the chat's 12/12 and the utility's 10/12). `MATH_MODEL` now defaults to
  the utility runner with `MATH_NUM_CTX=4096`, so compute costs no extra load and no
  reload. Its earlier retirement had rested on a tie plus a load cost that raising
  `OLLAMA_MAX_LOADED_MODELS` to 4 then removed; that is now measured rather than assumed.
- **The completion nudge is off by default.** It fires only on a turn that already
  needed an action, ran a tool, and *still* left it undone — and `nudge_bound` found the
  **first** nudge fired on 0 of 10 live write-class turns. It needed a ~9.5 GB model that
  `tev1:0.8b` cannot replace. `DECISION_COMPLETION_MODEL=nimble` re-enables it.
- **`.env` is the source of truth.** 38 of the 68 documented vars were forwarded by
  *neither* Compose file and silently fell back to `config.py`; both now forward the whole
  `.env`. And `TOOLS_ENABLED` defaulted to **`false`** in Compose while the code and the
  docs said `true` — a fresh install had **no tools at all**.
- **The user's own facts get provenance.** The chat path passed no `source_type`, so the
  user's share of memory was the bucket marked "unknown" (22% of frames). Measured
  read-only on both brains (`memory_source_split`): the **internet supplies 53–55%** of
  facts, the **user 22–23%**, and the **system's own bookkeeping 23–24%** — and 70% of all
  searches happen in *scheduled task runs*, not in live conversation.
- **New docs:** `docs/PIPELINE.md` (one turn end to end — stages, models, guards),
  `docs/COMPUTE.md` (the write-and-execute path, and that it is **not** a sandbox),
  `docs/UPGRADING.md` (rollout: nothing to migrate, the volume is never touched).

**Fleet:** 4 required models + 2 opt-in tiers. See `docs/MODEL_SELECTION.md`.

## v0.11.15-alpha

**Escalation stops sending mechanical work to the 27B, the MAX model is measured,
and the sandbox gets a host-visible folder.**

- **The auto-escalation was inverted.** A live turn — *"read muso-soup-offers.txt
  and then regenerate Muso Soup Priority.md"* — escalated to `qwen3.8:27b` with
  thinking on and ran **~22 minutes** to the 12-round cap. The trigger keyed on
  `_is_multi_step`, which fires on a sequence phrase like "and then": measured
  (`assistant/experiments/escalation_trigger/`) it fired on **100% of mechanical
  file tasks and 0% of hard reasoning**. It now keys on an **explicit reasoning
  cue** — re-measured **0% / 50%** — and the escalation and its source
  (user-toggle vs auto) are logged, which is what made the inverted trigger hard
  to see. The manual **Max** toggle is unchanged.
- **MAX stays `qwen3.8:27b`.** The opus Q4 9B was measured against it for the max
  role (`assistant/experiments/max_model_choice/`): on hard multi-step reasoning it
  scored **6/10 vs 9/10** and was only **1.25× faster** on the median — not the 2× a
  swap required. The earlier math-role probe used *eight easy* problems where both
  scored 8/8, a ceiling that cannot separate a 9B from a 27B. No `.env` change;
  `docs/MODEL_SELECTION.md` records why the swap is not re-proposed.
- **A host-visible shared folder.** `/app/data/shared` is bind-mounted to the host
  so files can be opened and edited with host tools — a spreadsheet edited in
  Excel, or a mailbox dump dropped in and read by the agent. It is nested inside
  the sandbox, so every file tool reaches it unchanged (`read_file("shared/…")`,
  `glob("shared/**")`). **Additive by design**: the volume, the DB, and every
  existing file stay where they are, so there is no migration and an update cannot
  orphan the brain. See `docs/FILES.md`.

## v0.11.14-alpha

**A filename the agent mentions now links on the live turn, not only after a
reload.**

- **The link appears immediately.** The chat refreshed its file list only from a
  `files-changed` event fired by a fixed set of tools, which missed an
  `append_file`/`rename_file` (neither was in the set) and a chat attachment
  upload (which dispatches no event at all). A file created or renamed that turn
  was therefore absent from the list, and the response's mention of it stayed
  plain text until a page reload. The list now refreshes on the turn's terminal
  `meta` event, so it is current when the answer is on screen, and
  `append_file`/`rename_file` are in the refresh set too.
- **The first fetch is no longer queued** behind the user fetch: the file-list
  load and its `files-changed` listener moved to their own synchronous `onMount`
  (the listener's cleanup was also registered after an `await`, outside the
  reactive owner).

## v0.11.13-alpha

**The Files page becomes an editor, the trace panel finally shows data, and a
filename in chat is one click from its contents.**

**Files page**
- **Rename.** A Rename button opens a modal (the extension is fixed); Copy name
  copies the display name. Rename and the agent's `rename_file` tool now share one
  implementation — and it fixes a real memory bug: it wrote the sandbox *path*
  into `file_name`, so a nested file's display name became `notes/x.txt`. It now
  sets the display name and the path separately and renames the CSV row children
  too.
- **Edit text in place.** An Edit button (text and markdown) opens a monospace
  editor; Save writes the bytes and refreshes memory through the one write path
  (`apply_file_to_memory`), so the profile and CSV rows stay true. A binary
  document is refused (415) — a textarea cannot round-trip its bytes.
- **Sorted by last updated**, newest first; the column shows the value it sorts by.
- **PDFs open in a new tab** — `?inline=true` serves `application/pdf` with no
  attachment disposition — instead of downloading.
- **The open file is marked in the list** (accent bar, `aria-current`) and the row
  is scrolled into view, so a deep link lands on it.
- **Back to chat**, and the chat links a filename mention straight to the file:
  `/files?file=<frame_id>`. Only names of files that *exist* become links (matched
  against the loaded list, never a filename-shaped pattern), so there are no dead
  links.

**Trace panel**
- Docked right like Settings (and mutually exclusive with it), and it **actually
  shows the turn**: task type, the memory context the agent used, citations, and
  the search backend/query. It was a placeholder that rendered `-` with no binding
  at all, and `memory_context`/`citations` never reached the message meta.
- The live turn only: the backend does not persist those per episode, so a
  reloaded turn shows the persisted search info and says so plainly.

**Agent**
- `edit_file` now steers the model to line-addressed edits ("PREFER
  start_line/end_line") — the path that cannot fail on an ambiguous or
  whitespace-drifted anchor, which is why editing a file through chat took several
  turns.

The file contracts (rename/save routes, the two names, text-only editing) are in
`docs/FILES.md`; the trace panel's scope is in `assistant/AGENTS.md`.

## v0.11.12-alpha

**Search now targets the right thing, shows real images, and searches when it
should — each measured, not assumed.**

Search is one of the project's two sources, so this release is about making it
*identify the target*, *learn from it*, and *render it well* — and proving each
change rather than trusting it.

**Targeting**
- **The query is distilled, never the raw instruction.** The query that leaves is
  the router's `search_query`, or `distill_search_query` (utility model), which can
  decline (`NONE`). The old `sanitize_query(request.message)` fallback is gone:
  stripping filler does not turn an instruction into a target, which is how
  scheduled tasks came to search for "Monitor and alert for Mozilla release
  dates…" and return "how to search" results.
- **The trigger is measured and fixed.** Across three classes the router searched
  unambiguous external queries (8/8) and stayed quiet on stable knowledge (8/8),
  but under-searched *borderline* queries — recommendations and "recent research"
  — at 3/10. The router prompt now treats a current source that answers *better*
  as a reason to search, and the router's explicit `wants_search=True` now forces
  search (symmetric with its veto) when memory is empty, overriding the reasoner's
  coarse length heuristic. All four numbers are now 100%
  (`assistant/experiments/search_trigger*/`).

**Imagery**
- **Brave's image index feeds the hero.** Web-result previews are always ~200px
  and their og:image is chosen for social sharing; Brave's image index returns
  images that match the *query*, each with a reliable ~500px CDN copy.
- **The hero prefers a real full-resolution image** and never upscales a ~200px
  preview into a banner — it renders near its natural size instead. Measured: ~76%
  of web results carry a distinct full image at 1200–2048px.
- **Web and image requests run in parallel**, so a Brave turn waits for the slower
  call, not for the sum.

**A negative result, kept**
- **No response cache.** 39% of stored queries repeat — but they are scheduled
  tasks (interactive repeat 1%, scheduled 76%), and a standing task must stay
  fresh. A query-keyed cache would have made every daily monitor report stale
  (`assistant/experiments/search_query_reuse/`).

The end-to-end search architecture and its contracts are documented in
**`docs/SEARCH.md`** — the pipeline, the query rule, backends, the relevance gate,
extraction, imagery, config, and the Brave image API limits.

## v0.11.11-alpha

**A faster math model on trial, a leaner image, and the fleet re-assessed.**

- **Math model switched, on trial.** A like-for-like comparison of the
  `sorc/qwen3.5-claude-4.6-opus-q4` family against the incumbents (same quant,
  matching sizes) found the Q4 9B matches `qwen3.8:27b` on executed-answer
  accuracy, is ~4–10× faster per problem, and is 6.6 GB instead of 17 GB. It is
  now `MATH_MODEL` in dev and prod. Chat and utility were checked too and **kept**
  (a mechanical tie with no quality win; fewer facts extracted, respectively). The
  probes are in `assistant/experiments/model_fleet_q4/` and
  `assistant/experiments/chat_quality_q4/`; rollback is one line in `.env`.
- **`/health` reports the math role.** The fleet dict omitted `math`, so the model
  serving the `compute` tool was invisible; it now appears (or `"(disabled)"`).
- **Dependency audit.** Removed `sqlalchemy`, which nothing imported (the store is
  raw aiosqlite) yet was installed into the runtime image *and* the dev extras.
  Removed the stale `@types/dompurify` / `@types/marked` — both libraries ship
  their own types now — and moved `@types/d3` / `typescript` to `devDependencies`.

The fleet decision and its measurements live in `docs/MODEL_SELECTION.md`.

## v0.11.10-alpha

**The context window is now budgeted, not guessed — and long conversations keep
their thread.**

The tool loop shares one window between the system prompt, every tool schema,
history, the tool results, and the answer. v0.11.9 made that window *visible*;
this release makes it *budgeted* and bounds what enters it, then fixes the two
places a long conversation lost continuity.

**Context throughput**
- **The window's composition is logged.** `context_fixed` reports the system
  prompt, tool schemas, history, and tool-result characters next to the peak
  prompt (`context_usage`), so a turn's cost is a measurement, not an inference.
- **A tool result is capped at a derived allowance** — `window − measured fixed
  cost − reserved answer` — replacing a flat fraction of the window. The
  allowance adapts: a longer history or a bigger tool set shrinks it.
- **The rounds share that allowance.** When a new tool result would exceed it, the
  oldest is collapsed to a marker the model can see (and the
  `tool_call`/`tool_result` pairing is preserved), so a long tool chain cannot
  overflow the window.
- **Loop length follows the task.** `max_tool_rounds` (6) is a runaway guard, not
  a task budget — the model ends the loop itself — and the reasoner's
  think / max-intelligence escalation gets 12.
- **History is bounded by size**, not only by turn count, so one huge prior turn
  cannot fill the window by itself.
- **The system prompt no longer duplicates the tool schemas.** The prose list was
  stale (eight of nineteen tools); the schemas are the contract, and the chaining
  examples and uploaded-file note remain.

**Conversations**
- **A long thread can recall its own middle.** The retriever excludes only the
  turns already carried verbatim, not the whole current session, so older turns
  are recalled by meaning.
- **Summaries track the present.** Summarization now summarizes the recent turns
  and folds in the prior summary, instead of summarizing the opening of a long
  session and never advancing.
- **Summarization is visible.** `GET /chat/session/{id}/summary` (read-only,
  owner-scoped) and the session summary on the turn `meta`, shown in the chat's
  "What I learned" panel.

**Cleanup**
- Removed the dead non-streaming tool loop (`run_tool_loop`) and its parallel
  execution machinery — every turn goes through `stream_tool_loop`.
- Test-suite audit: dropped redundant tests, strengthened weak ones, and made a
  permanently-skipped end-to-end test runnable on demand.

Policy and contracts: `docs/CONTEXT_THROUGHPUT.md`, `docs/CONVERSATION_SCALING.md`,
`docs/FILES.md`.

## v0.11.9-alpha

**Large files stop overwhelming the agent — and edits get precise.**

The tool loop sends the system prompt, every tool schema, and the history in one
prompt, so it is the largest prompt in the system and the one that overflows
first. A single `read_file` of a 43k-char CSV was ~15k tokens against a 16,384
window; llama.cpp truncated it silently and the model answered from a fragment it
could not see. This release makes the window visible, budgets what enters it, and
gives the model handles to work with a large file instead of holding it.

- **The context window is visible.** The turn's true peak prompt is read from
  Ollama (`prompt_eval_count`) and shown in the trace as a context meter, with a
  warning when a turn actually reached the window.
- **A read is budgeted from the window, not a flat number.** The old cap was
  60,000 chars (~15k tokens) — nearly the whole window. A read now gets a
  fraction of `CHAT_NUM_CTX`, and a capped read carries an **actionable** marker:
  the line range, the true total, and the exact `read_file(path=…, offset=…)`
  call for the next page.
- **`read_file` pages by line** (`offset`/`limit`), and a requested page says
  where it is (`[lines 201-250 of 628]`).
- **Build and query a file without reading it.** `append_file` adds a line
  without pulling the file into the window; `search_file` answers "is this
  already here?" by matching lines. Dedup is composition, and neither assumes a
  format.
- **A file arrives with its shape.** A deterministic profile — rows, columns,
  per-column distinct counts, and the category distribution for low-cardinality
  columns; keys and sizes for JSON; headings for markdown — is computed at write
  time and shown in memory context, so "what is this file and where are the
  gaps?" is a memory read rather than a model guess.
- **Editing is precise.** `edit_file` takes a line range (the numbers `read_file`
  shows) or a text anchor. An ambiguous anchor is refused with the line number of
  every match instead of silently rewriting the file, and `replace_all` now
  defaults **off**. Given both, the anchor is verified against the range before
  replacing — a stale line number cannot edit the wrong line. An edit refreshes
  the file's memory through the same step writes use, so its profile and row
  frames no longer go stale.

Contracts live in `docs/FILES.md`; the tool-loop window rule in
`assistant/AGENTS.md`.

## v0.11.8-alpha

**Browsing works now, and can read further into a site.**

- **robots.txt was blocking almost everything.** The check was a substring match:
  `"disallow: /"` is a substring of `"disallow: /private/"`, so any site with a
  single path-specific rule was treated as *fully* disallowed — which is nearly
  every site, and why the agent was "often blocked by robots.txt" while nothing had
  actually disallowed it. It is now parsed with `urllib.robotparser`, per
  User-agent and per path, against our `AssistantBot` token.
- **`fetch_url` can go deeper.** A new `follow_links` (0–5) reads further into the
  same site — same-host links only, deduped, robots-checked per URL, paced between
  fetches, and SSRF-checked on every hop.
- **One fetch path.** Search enrichment used its own fetch (`follow_redirects=True`,
  no robots check, no per-hop SSRF check); it now shares the tool's `_fetch_page`,
  so both obey the same rules — which also closes the SSRF gap.

Policy and limits are documented in `docs/FETCHING.md` (including the honest-UA
stance and the AI-crawler-block intent caveat).

## v0.11.7-alpha

**Conversations download as text, and the voice status tells the truth.**

- **Download a conversation.** The conversation menu has a Download action; it
  saves the whole thread as a plain-text transcript (`.txt`) — a header, then each
  turn labelled with its role and timestamp, with the loop's scaffolding (sources
  footers, memory markers) stripped. Owner-scoped, and useful for carrying context
  to another brain.
- **"Transcribing..." now shows while it transcribes.** The voice-mode status was
  tied to the recorder teardown (a few milliseconds), so during the actual
  `/transcribe` request the bar read "Listening...". It now reads "Transcribing..."
  for the whole request — while the mic stays open, so a sentence said
  mid-transcription is still queued rather than lost.
- **Speaking beats listening.** While the agent read its answer aloud the bar
  showed "Listening..." (the mic is shut then). It now shows "Speaking...".
- **Wider file preview.** The file viewer was a fixed 420px column; it now scales
  with the viewport (up to 720px), so a document is readable without scrolling
  sideways.
- Cleanup: removed a never-set `'speaking'` voice status and its unused helpers.

## v0.11.6-alpha

**Calendars are real calendars now, and files can be renamed.**

- **Multi-event `.ics`.** Writing a calendar no longer collapses it into a single
  event titled with the first line (which could itself be `BEGIN:VCALENDAR`, with
  the real events escaped inside its description). The model supplies a full
  iCalendar document; it is stored as-is, validated and formatted by the
  `icalendar` library, so a list of dates becomes a list of events. Plain prose
  still becomes a single event.
- **Reading a calendar shows the calendar.** `read_file` on an `.ics` returns the
  raw document instead of a five-event summary, so the agent can inspect and edit
  one — the summary could not be turned back into a calendar.
- **`rename_file`.** A file can be renamed in one step: the sandbox file and its
  memory frame move together, so the old name stops resolving and the new one
  works immediately. The extension is fixed and an existing file is never
  overwritten.
- **Consistent file-content policy.** "Memory holds what a file *is*, never what
  it *contains*" now holds on both write paths, and `list_files` no longer serves
  a stale content preview. Existing content slots are left in place, read by
  nothing — nothing is deleted.

## v0.11.5-alpha

**Files table: dropped the Type column.**

The column showed the frame type — `entity` for every row — which told the user
nothing, and the extension is already part of the filename. The table is now
Name (icon + filename), Size, Date, Actions. The Size column stays: it is no
longer complicated, since `/files/list` returns `file_size`.

## v0.11.4-alpha

**The Files page grew up: real sizes, real downloads, real previews.**

- **Sizes were `NaN`.** `GET /files/list` never returned the `file_size` slot,
  so every row rendered `NaN MB`. The list now carries `file_name`, `file_ext`,
  and `file_size`, so the grid shows a real size and the extension as the type.
- **Download each file.** New `GET /files/{frame_id}/download` serves the
  original bytes as an attachment, with a Download button on every row and in
  the viewer. It goes through the bytes, not the `/content` route — that decodes
  as UTF-8, which turns a PDF or docx into garbage.
- **Binary files no longer "preview" as text.** Only text extensions are shown
  as content; anything else shows "No preview" with a download link.
- **Markdown previews as rendered HTML** (`marked` + `DOMPurify`), not raw text.
- **Delete asks first.** A confirmation modal replaces the immediate delete.
- **Filenames truncate with an ellipsis**, and the icon has room before the name.
- Fixed a class collision: the grid's table header shared `.files-header` with
  the page header, so the grid rules were overriding the page header's layout.

## v0.11.3-alpha

**Cleanup: the read-aloud code was fine — the browser tab was muted.**

v0.11.2 bundled read-aloud changes that were chasing a symptom with no code
cause. They are removed, so the diff only contains changes that fix something
real.

- **Kept — the voice setting "sticks".** The voice `<select>` re-applies the
  stored value once Chrome's asynchronous voice list arrives. Verified against a
  real browser: without it, a reload keeps `voiceUri` in storage but the dropdown
  displays a different voice, which reads as "my setting didn't persist".
- **Kept — voice mode listens through transcription.** Captures are queued and
  sent serially, so speech between utterances is not lost.
- **Removed — speculative read-aloud changes.** Utterance retention, the `lang`
  fallback, the "mark only when enabled" rewrite of the read-aloud effect, and
  the finite-value guard. None fixed a demonstrated bug; `ChatPage.tsx` and
  `voice.ts` are back to their v0.11.1 state.

## v0.11.2-alpha

**Voice mode listens like it should, and read-aloud actually reads.**

Three voice-output defects and one structural listening gap, all found by
tracing the settings and audio paths end to end.

- **Read-aloud plays.** The spoken utterance was never retained, so a
  garbage-collected utterance could stop mid-sentence and never fire
  `onend` — which left the "agent is speaking" gate stuck true, keeping the
  microphone shut. The utterance is now held until it finishes, and gets a
  `lang` fallback for engines that drop one with neither voice nor lang set.
- **Turning read-aloud on works.** A reply was marked "spoken" even when
  read-aloud was off, so switching it on could not read the answer that
  prompted the switch. It now marks only what it speaks.
- **The voice setting sticks.** The voice `<select>` set its value before
  Chrome's asynchronous `voiceschanged` populated the options, so the
  browser dropped it to "" and the panel showed the wrong voice — looking
  exactly like the setting had not persisted. It now re-applies the saved
  value once the options exist. (Persistence itself was already correct.)
- **Voice mode listens through transcription.** The microphone used to be
  shut for the entire `/transcribe` round trip, so anything said between
  utterances was dropped. It now stays open whenever voice mode is on and
  the agent is not speaking; captures are queued and transcribed one at a
  time, so order is preserved. A busy agent still queues the transcript,
  and queued items remain removable while you wait.

## v0.11.1-alpha

**Following through on v0.11.0: the task loop's edges are cleaned up.**

Running tasks through the real loop was right, and it surfaced three rough edges
at the seams — each a small correctness issue rather than a redesign.

- **An alert no longer swallows the sources footer.** Because a task's report now
  carries the loop's own footers, a `**Sources:**` list (or the "answered from
  memory" marker) landed *after* the `ALERT:` line — and the parser treats
  everything from that line on as the alert body. The sources became the alert's
  "reason" and dropped out of the stored summary. Footers are now stripped before
  the alert is parsed.
- **Scheduling a task is consent.** A scheduled task used to hit Brave's
  sensitivity gate and, finding no one present to answer, would replace its
  output with a "do you want to proceed?" prompt. The user asked for the search
  when they created the task, so the task's request now carries `search_consent`
  and the gate is skipped.
- **Run-now has room to finish.** The `run_scheduled_task` tool had a 60s cap,
  but a run-now executes the whole loop (routing + forced search + tool loop +
  generation) and a single LLM call may take 600s. A big task was cut off
  mid-run. The ceiling is now `SCHEDULED_TASK_TIMEOUT_SECONDS` (default 900s);
  the scheduler's own firing path still has no cap.

## v0.11.0-alpha

**Scheduled tasks run the real loop now.** The daily list was executing through a
third, partial copy of the pipeline — and it showed: the job-postings monitor
searched with its own instruction as the query, so Brave returned documentation
for job-search *tools* instead of postings.

- **One loop, three consumers.** `run_scheduled_task` is now a thin wrapper over
  the same loop as chat and the UI stream. A task's script drives routing and
  retrieval, is delivered as the explicit user turn, and search is forced. The
  ~160-line duplicate pipeline is gone.
- **Tasks can search properly, and use tools.** Because it is the full loop, the
  model can `recall` the user's criteria and `web_search` a targeted query while
  a task runs — it had **no tools at all** before. The search query is the
  router's, not the instruction.
- **The agent's cry for help was correct.** "Search results surface tool
  documentation" was the presence rule working: it could not settle the task and
  said so. It can now do better — and still alerts when it genuinely cannot.

### Behaviour changes

- A task run logs its script as a **user** turn as well as the answer (the full
  loop logs both turns). The answer is still the assistant episode the scheduler
  links to the daily-run frame.
- Facts are **no longer mined from the task's own report**
  (`extract_facts_from_document` on the output). That was self-referential and
  redundant with search extraction; the report is already an episode.

## v0.10.1-alpha

**Editing an existing file works, and failing to edit tells you why.**

The agent could create files but not reliably change them — found when it tried
to update a release document and reported "the edit didn't find the exact text
to replace."

- **A binary document could not be edited at all.** `read_file` shows a `.docx`'s
  text, but `edit_file` replaces bytes on disk — and a `.docx` is a zip — so the
  text could never match, and the error was a misleading "old_text not found".
  `edit_file` now refuses a binary document and says what to do instead: read it,
  then rewrite the whole file with `write_file` (which re-renders a real document
  and refreshes memory).
- **A near-miss edit dead-ended.** A collapsed blank line or a trailing space was
  enough to defeat an otherwise-correct edit, with nothing to correct it with.
  Matching is now whitespace-tolerant (words must still match exactly), and a
  failure names the file size and the closest region so the model can retry
  precisely.
- **The guidance was wrong.** The tool description told the model to use
  `edit_file` for "updating documents", which is the path that cannot work. It
  now says `edit_file` is for text files, and documents are read and rewritten.

## v0.10.0-alpha

**The agent knows what day it is, and there is one loop instead of two.**

### It knows the date and time

The model had no clock, so "what's today's date?" was answered from its training
cutoff. A current date/time line is now injected into the system prompt — in the
user's timezone, resolved the same way the scheduler resolves it
(`DAILY_TASKS_TZ` > `TZ` > host), so a US user asking at 19:00 is not told it is
tomorrow. It is injected rather than exposed as a tool, so it costs no
round-trip. The `.ics` writer now stamps the user's date too, not the container's
UTC date.

### One cognitive loop

`chat()` and `chat_stream()` were two ~600-line copies of the same loop, kept in
step by hand. They had drifted **six** times, each a user-visible bug: a stalled
search gate that hung the live stream, a generation failure that escaped instead
of degrading, a `compute` result shown and never stored, a missing sources
footer, a missing learning summary, and a correction turn logged twice.
`chat_stream()` is now a pass-through to one loop (`_run_turn`) and `chat()`
drains it, so a change reaches both by construction. The best-tested path is now
the one production runs.

Closing the drift also fixed three parity gaps: the stream now carries
`citations` and `memory_context` (so the CLI and `/chat` adapter lose nothing),
scheduled-task and correction turns emit their metadata like every other turn,
and a streamed turn's reasoning trace is persisted instead of dropped.

### Known limits

- **The date is a snapshot of the turn.** It is read when the prompt is built,
  not per-token; a turn spanning midnight reports the day it started.
- **`run_scheduled_task` is still its own path.** Deliberately simpler
  (always-search, no routing/extraction); not folded into the one loop.

## v0.9.1-alpha

**The suite and the docs stop drifting.** A maintenance release — no feature
changes.

- **Test audit.** Removed eight tests that could not fail (empty bodies), fixed
  one that always skipped, renamed a fixture that was collected as a test, and
  strengthened several whose only check was "does not raise". A new
  `test_suite_hygiene.py` fails the suite if any test has no assertion, so this
  cannot regrow.
- **Docs reconciled with the code.** The prod port (8444, not 8443), two
  `assistant db` commands that do not exist, the backup schedule's real name and
  interval, and the max/math model defaults (opt-in, not `qwen3.8:27b`).
- **Changelog tightened** from 701 to 254 lines, with its duplicated README tail
  removed.

## v0.9.0-alpha

**When you ask it to save something, you get a file — and you can find it.**

`write_file` wrote the text with a document extension: a `.docx` was the literal
string and the owning library called it corrupt (`PackageNotFoundError`;
`BadZipFile` for xlsx/odt/ods/odp; `.xls` found the raw CSV text where a
spreadsheet header belongs). The agent now renders a real file for all 13
supported formats and refuses one it cannot produce. Separately, the Files page
listed only *uploaded* files, so everything the agent wrote was invisible though
it was on disk and in memory; both kinds are listed now.

- **One path, not two.** An upload recorded extracted entities and rows; an
  agent-written file recorded only its name and size. Both now run the same
  memory step.
- **Format set:** thirteen, the same for reading and writing. `.md` added;
  `.rtf`/`.eml`/`.tsv`/`.html`/`.xml` dropped from upload (files already saved
  still read).

**Known limits:** PDF is generated text, not layout; an `.ics` write is a single
event; prose written to `.json` is wrapped as `{"content": ...}` to stay valid.

## v0.8.2-alpha

**The names are not swapped any more.** v0.8.1 fixed the extractor's routing but
not the correction pipeline, which took the model's frame and key verbatim and was
the other road a name travels. *"I'm not Carl. You are Carl."* was applied as
`user_identity.name = "Carl"` — the assistant's name on the user's frame, under a
key nothing reads — while *"My name is not Carl. I go by Boz."* left the user's
name on `identity_name`. Both were stored, in each other's place.

- **The correction model says who.** `CorrectionResult` gains `subject`
  (`user` | `assistant` | `topic`); the prompt asks for it.
- **The code maps that answer to a frame.** `route_correction` sends
  `user`/`assistant` to the reserved frame, normalises any name key to
  `full_name`, and leaves `topic` alone. No frame-name list, no value guessing.
- **Routing runs before validation**, so a correction is checked against the
  frame it will be written to.

**Known limits:** existing memory is not rewritten (a rename corrects it); the
subject is still the model's judgment, and the correction path has no cross-check.

## v0.8.1-alpha

**The assistant stopped taking your name.** v0.8.0 made the assistant's name
yours to set; the first thing it did was adopt the name you gave yourself. The
extraction guard proves *where* a name came from, not *who* it is for, and the
naming rules said nothing about the user's own name — so "my name is not Carl, I
go by Boz" renamed the assistant to Boz. The same gap had already let "Jay Miles"
(from a pasted email) and the pronoun "you" onto the assistant's name.

- **The user's own name has its own frame:** first-person self-identification
  extracts to `user_identity.full_name`; the assistant's name only when you
  assign it to the assistant.
- **Names in pasted content are not identity facts.**
- **A name cannot belong to both:** a same-turn claim for user and assistant
  keeps the user's and drops the assistant's.
- **The two frames resolve by exact name only**, so canonicalization cannot merge
  them; "what is my name?" retrieves `user_identity`.

**Known limits:** existing memory is not rewritten; extraction remains the model's
judgment.

## v0.8.0-alpha

**A name that sticks.** You could tell the assistant its name, watch it
acknowledge you, and still find the old name on the tab. The name was never
really yours to set: a conversational fact carries source reliability 0.5, the
stored name had been left at 0.99 by an earlier correction, and the confidence
ladder keeps the higher-reliability value. The attempt was recorded as an
auto-resolved conflict, so the brain looked like it had chosen otherwise.

- **The assistant's name is authoritative.** A user-stated `full_name` is written
  at reliability 1.0 and always supersedes the stored name — still through
  `revise`, `slot_history`, and the conflicts table, not around them.
- **The tab follows memory.** The name endpoint was right; the UI asked once on
  mount. A turn that reports `identity_name.full_name` now makes the app re-read
  it, so the header and tab update in the same turn.
- **A name made only of function words is rejected** (the live brain had mined
  "you" as the name).

**Known limits:** a mis-extracted name now sticks harder — the guard filters
self-description and pronouns but is not infallible, by design (the project stays
model-first). Only `identity_name.full_name` is affected.

## v0.7.0-alpha

**One picture instead of six.** Search answers came with a strip of up to six
images behind a lightbox, and the pictures were often wrong. The gallery is gone:
one hero at the top — the video when you asked for video, the lead image
otherwise — with other videos as links. It also turns out the image vanished on
every reload.

- **The gallery was 300 lines of component and 635 of stylesheet** for pictures
  not good enough to earn the space. Net −720 lines.
- **The hero never survived a refresh** because the schema parsing a restored
  message declared three fields and `z.object()` strips undeclared keys — so
  `search_info` was deleted after the backend had sent it, taking the "Searched
  via Brave" badge with it. The fix carries a test checked to fail without it.
- **Dangling doc citations:** fifteen comments cited deleted planning documents;
  they now point at the rule's real home, and a test fails if a citation dangles.

**Known limits:** this changes **how many** pictures you see, not **which** one.
The hero is still the search engine's first-ranked result, judged from filename
and host — a sponsored image with a clean filename still wins. Scoring images by
embedding similarity is being measured before it is built on.

## v0.6.0-alpha

**Files actually work now.** The assistant could read a document at upload and not
at read, a large one was refused outright, it printed raw database ids, and its
For You bell filled with things that were never questions.

- **Documents are read, not decoded as text.** `read_file` bypassed the extractor
  and decoded every file as UTF-8 — binary noise for a PDF or `.docx` — and past
  1 MB it was refused first. Every document format now reads through the same
  extractor upload uses; a scanned PDF with no text layer says so; a corrupt file
  is not reported as missing.
- **The input bar uploaded bytes**, not text, so a correctly-extracted PDF no
  longer arrived corrupted. Its format list was six entries against the files
  page's seventeen; both now derive from one list, pinned by a test.
- **No file-size limits.** Removed the 1 MB read cap, the 10 MB write cap, and
  three upload guards. The only bound is the model's context window on a read,
  marked with the true total.
- **No database ids in replies.** Graph edges render the target's name, not
  `frame:4832`; an unnameable edge is omitted.
- **For You can be cleared.** The bell was full of "Correction applied" notices
  fired during the conversation you were in. Those writers are gone; alerts come
  only from scheduled work. The green stripe was not "resolved" — `info` now uses
  a neutral colour — and the never-read `is_read`/`read_at` fields are removed.
- **Container resilience.** The healthcheck probed `/health`, which calls Ollama
  and can block when it is down — so an Ollama outage restarted a container a
  restart cannot fix. It probes `/healthz` (liveness, no I/O) now; `autoheal` is
  label-scoped; logs are rotated.

## v0.5.0-alpha

**The alerts channel stops looking like a warning.** These are things to bring up
and discuss, not errors — so the bell is **For You**, in mint, and resolving one
puts it in the conversation you chose.

- **Resolving posts the alert's question** into the chosen conversation, whether
  or not it had history (it was written only into empty sessions, so picking a
  used conversation showed nothing). One modal, not two; the wall of text is gone.
- **The stale frontend that made rebuilds look broken:** the Dockerfile copied
  the fresh bundle, then `COPY assistant/` re-copied the committed `static/` over
  it, so prod served a pre-top-bar frontend. The build output is excluded now.
- **Class collisions across global stylesheets** (`.empty-state`, `.voice-dot`,
  `.file-icon`) are scoped or removed, with a guard against a bare class defined
  twice.
- **Dev frontend mount:** a file bind mount pins an inode, so any checkout left
  Vite reading a deleted file; it mounts the `frontend/` directory now.

## v0.4.0-alpha

**One top bar, and the small lies it was telling.** The header is a single
coherent control strip, and two places that showed an untrue state are fixed.

- **One shared style** (`.topbar-btn`), an inline SVG icon per control coloured by
  `currentColor`, a deliberate order, and the voice status bar taking Let's talk's
  place while a turn is live.
- **"Transcribing…" now appears during a conversation turn** (the capture hook
  never advanced the global voice status, so it read "Listening…" throughout).
- **Resolving an alert into the conversation you are viewing now shows it** (the
  message-loading effect tracked the active id, so re-opening was a no-op).
- **One dependency source:** `pyproject.toml` omitted `sqlcipher3`,
  `cryptography`, `faster-whisper`, `python-multipart`, so `pip install -e .`
  produced no encrypted brain. Declared now; the Dockerfile builds from `.[dev]`.
- **Icons are inline SVG, not emoji.**

## v0.3.0-alpha

**It reads your documents, and it remembers what it showed you.** The assistant
opens to the files a household actually has — PDF, Word, Excel, PowerPoint and
the legacy formats around them — and stops the chat losing its imagery on reload.

- **It reads documents:** PDF (`pypdf`, first 50 pages), `.docx`, `.xlsx`,
  `.pptx`, plus `.rtf`, OpenDocument (`.odt`/`.ods`/`.odp`), legacy `.xls`,
  `.eml`, `.tsv`, and iCal via `icalendar`. All offline and pure-Python; XML is
  parsed with `defusedxml`; extraction runs off the event loop. Legacy `.doc`/
  `.ppt` say to re-save as `.docx`/`.pdf`.
- **The chat remembers its media:** a search turn stores a compact projection of
  its results (titles, URLs, thumbnails, video fields — never page bodies) so a
  reload restores it.
- **Links ask first** — a link leaving the assistant names the host and confirms,
  including links rendered inside an answer.
- **Two UI defects:** alerts stopped flickering (a 30s poll swapped the list for
  a spinner), and a new conversation is no longer buried below the fold.
- **Accuracy:** the summary frame was never indexed (the summarizer passed an
  `EmbeddingResponse` where a vector was expected); idempotent hops retry while
  side effects do not; the encrypted DB retries a transient key-derivation read.

## v0.2.0-alpha

**The brain stops lying to itself.** Four plans shipped, one closed on
measurement: memory that recorded decisions as undecided, files that duplicated
their own contents, and a bell full of notices nobody could act on.

- **Alerts are memory** — a frame of type `alert`, not a notifications row, so
  retrieval can raise one when it is contextually relevant. The **presence rule**
  decides when: an alert is warranted when the agent learned something and the
  user was **not there to hear it**. A task completion is not an alert; a task
  *failure* is. An alert closes by being answered in a conversation, not by being
  read — there is no read flag, and none is coming back.
- **Files: memory holds what a file *is*, never what it *contains*.** Four sites
  still wrote content, including `read_file` on every read, and a missing file
  returned a stale truncated copy the model answered from. Content keys are now
  refused at write time by `upsert_slot`.
- **Write-path hygiene:** blank frame names / slot keys / values are refused at
  `upsert_slot` (the one funnel); the summarizer writes its counters directly
  instead of "conflicting" with itself; `POST /tasks/run-due` returns
  immediately; embeddings migrated to a single model.
- **Conflicts: the ladder works.** A long-held belief that it never
  discriminated was wrong — a user fact (0.99) beats a search attempt (0.5), and
  the apparent recency-always-wins came from *ties* resolving by recency.
  `EXISTING_WINS` is recorded as decided (`auto_resolved` with `resolved_value`),
  not deferred, and every conflict records its decision inputs. Plan D (a model
  that reasons over conflicts) was closed: it would duplicate a working comparator.
- **Supplied content is registered as memory** (a pasted list used to survive one
  turn); `web_search` stays available on every turn — three static-rule fixes were
  tried and reverted. The model decides.
- **Process:** plans are wiped when shipped; experiments are pre-registered with
  falsification conditions fixed before data.

**Known gaps:** the journal (Plan E phase 3) is not started; conflicts the ladder
declines to settle remain `pending` (now distinguishable from decided ones);
portable brains are implemented and tested but reachable by no endpoint or CLI
command.

## v0.1.1-alpha

**Memory is never forgotten on a timer.** The background garbage collector is
gone, and the maintenance that remains is cheaper and more predictable.

- **Time-based decay is gone.** It only ever lowered priority on rows already
  below the default — data the user had already forgotten — so it re-forgot
  things on a delay. Memory leaves only through an explicit `forget` or deletion.
- **Merging is ad hoc; backups are not.** Duplicates merge as soon as found (the
  6-hour tick is a *look* cadence). The snapshot moved to its own 12-hour clock;
  a merge without a fresh snapshot takes one first.
- **Faster re-indexing:** embedding top-up runs every 6 hours as its own job.
- **Removed:** the `assistant db gc` command and the `/plans/` archive convention.

## v0.1.0-alpha

**The first alpha.** A privacy-first cognitive digital assistant that remembers
what you tell it, learns over time, and corrects itself when it is wrong. All
inference runs locally through Ollama; nothing leaves your machine unless you
explicitly opt into an external search backend.

This is alpha software for a **single household**, not hardened for untrusted
multi-user input or exposure beyond localhost.
