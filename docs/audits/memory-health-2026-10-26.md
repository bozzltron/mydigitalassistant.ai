<!-- Point-in-time audit, kept as the baseline finding set. The agent that
     produced it has since been reframed to review memory CODE, not the live
     brain; the findings below remain valid and are the source of R1-R11. -->

# Memory health audit — 2026-10-26-mha01

## Verdict

**Degraded — the core ladder, vector fleet and backup cadence are operating, but the live brain has unsafe merge candidates, stale file memory, forgotten values still reachable by search, and inspection paths that do not always show what the model actually used.**

Snapshot: 2026-10-27T01:15:17Z (the run-id date is the local date). The brain remained live; episodes moved from 5,561 to 5,562 during the audit, so counts below are point-in-time readings, not one transactional snapshot.

## Method and evidence boundaries

- Queried prod with `curl -sk` against `https://localhost:8444`: `/health`, `/memory/frames`, `/memory/conflicts`, and `/memory/search`, supplying `user_id=1`.
- Ran `python -m assistant.cli.app db status`: `/app/brain/assistant.db`, **163,893,248 bytes**, vector search available.
- Ran `python -m assistant.cli.app db consolidate` **without `--execute`**. It explicitly ended `Dry run — no changes written.` No consolidate, re-embed, migration, forget, backup, export or restore was performed.
- Additional hygiene counts used SQL through a URI with `mode=ro`, followed by `PRAGMA query_only=ON` (verified `query_only = 1`). No schema initializer or store write method was called.
- Log filter was `--since 168h`, but the current container's available history starts at its 2026-10-25 startup. Thus the log evidence covers the available current-container window, not a guaranteed full seven days.
- Restore portability was reviewed in code but **not executed**, because both export and restore write files. `PRAGMA quick_check` returned `ok`; that is not a foreign-key or restore-proof check.

Evidence notation below: **API** = HTTP result, **RO** = read-only SQL result, **LOG** = prod log line, with the relevant code path named where it explains the result.

## Subsystem reconciliation

| Subsystem | Verdict | Evidence | Principal risk |
|---|---|---|---|
| Schema and invariants | Degraded | RO: 5,846 frames / 10,692 slots / 6,041 associations / 5,562 episodes; `quick_check=ok`, but 57 `slot_history` rows and two episode vectors are orphaned. | Audit history is not fully parented (R9). |
| Write path and confidence | Degraded | API: 1,538 conflicts = 1,263 `auto_resolved` + 275 `pending`; all auto rows have a winner and resolution time. RO: 12 current `user` slots nevertheless have NULL reliability. | Derived values enter the ladder (R2), and ordinary revision can lose the user's reliability rung (R4). |
| Read path and prompt | Degraded | RO: active-frame vector coverage is 5,775/5,775, but 140 frames have newer slots than vectors. API: a forgotten-value query returns priority-0 slots. LOG: 10 memory refits while meta uses the pre-fit rendering. | Stale/unretired evidence and inaccurate transparency (R3, R5, R7). |
| Hygiene | Degraded | Dry-run: 277 clusters, 74 merges planned from the first 50, 227 clusters deferred. RO: 1,544 zero-slot frames, but 1,539 have edges; only five are both slotless and edgeless. | Merge precision, not bulk deletion, is the problem (R1, R8). |
| Lifecycle and backups | Contained / degraded | LOG: five consecutive consolidation checks skipped `74 merges > cap 10`; clusters moved 273 → 277. Three recent scheduled snapshots are present; newest filename is `assistant-pre-scheduled-20261026-220220-b3a770.db`. | The cap contains the unsafe plan but also prevents all progress (R1). |
| Fleet integration | Mostly healthy | `/health`: Ollama reachable; utility `qwen3.5:4b`, embedding `qwen3-embedding:0.6b`. RO: every vector is labelled with that model and is 4,096 bytes = 1,024 float32 dimensions. | Model identity agrees; refresh freshness does not (R7). |
| Summarizer | Degraded | RO: 35 summary frames, zero summary-content conflicts; 36 eligible sessions but only 34 covered, and six stored summaries lag their episode counts. | Recent-first reruns can starve older eligible sessions (R11). |
| HCI | Degraded | Correction and per-frame pending-conflict resolution exist. API/RO: 71 tombstones appear in the frame list; forgotten values remain searchable; 275 pending rows have no global queue consumer. | Forget, review and “what was used” are not fully truthful or reachable (R3, R5, R10). |
| Observability | Degraded | LOG: 33 `context_fixed`, 32 `context_usage`, 10 refits and one hard system-prompt truncation; no embedding warning lines. Existing embedding staleness SQL reports zero while the timestamp lag query finds 140. | Add content-refresh lag, not another model judgement (metric below). |

## Hygiene metrics

Thresholds are audit guards, not claims that every deviation is wrong. In particular, zero-slot frames can be useful graph nodes and repeated values under different keys are candidates requiring review, not automatic duplicates.

| Metric | Result | Threshold / interpretation |
|---|---:|---|
| Total frames returned by `GET /memory/frames?user_id=1` | **5,846** | RO independently counts 5,846 total rows. Only user 1 exists, so owned + shared returns the whole brain. |
| Live frames (`deleted_at IS NULL`, `priority > 0`) | **5,775** | Baseline for coverage rates below. |
| Tombstoned frames | **71** (16 owned, 55 shared) | Expected in raw storage; **0** should be presented as ordinary live frames. |
| Live frame types | entity **4,180**; concept **644**; event **444**; record **282**; alert **77**; household **38**; conversation_summary **35**; scheduled_task **21**; metric **10**; location **7**; article **6**; establishment **4**; model **4**; document **3**; organization **3**; person **3**; phrase **3**; user_content **3**; algorithmic_strategy **2**; correction **2**; website **2**; function **1**; software **1** | Full RO distribution. Entity is 72.4% of live frames; this is a review signal, not a deletion rule. |
| Frames with zero slots | **1,544 / 5,775 = 26.74%** | Initially above a 10% review guard, but **1,539 have associations** and can serve the measured graph walk. Only **5** have neither slots nor edges; do not bulk-delete the 1,544. |
| Blank live frame names | **0** | Meets the non-blank invariant. |
| Blank slot values | **10**, all `description` | Store-level `upsert_slot` rejects new blanks; derived/direct paths still permit them. Low count; review whether an absent description should instead be absent. |
| Exact placeholder tokens | **36**: 17 `unknown`, 16 `n/a`, one each `tbd`, `null`, `none` | 0.34% of slots. These are exact-match candidates, not proof each is meaningless (`none` can be a legitimate answer). |
| Same frame + normalized value under multiple keys | **407 candidate clusters** | Review guard only. Example: `why_not` holds “<redacted name>” under eight keys; `mozworth` holds “mozworth” under seven. Same-turn `(frame,value)` dedupe does not remove historical cross-writer duplicates. |
| Dry-run duplicate plan | **277 clusters; 5,210 frames scanned; 74 merges from first 50 clusters; 227 clusters not planned** | Any clearly distinct pair is a stop condition. The plan contains several; do not execute it. |
| Conflicts | **1,263 auto / 275 pending** | Auto decision completeness is **1,263/1,263**: 463 existing wins, 800 new wins, zero missing winner/time. Pending rows are deferrals, not completed decisions. |
| Pending backlog age and state | 2026-08-21 through **2026-10-01**; current slot equals existing **164**, new **31**, neither **80** | A healthy queue should have a consumer and trend downward. Current values cannot safely retroactively prove the original decision. |
| Missing conflict reliability inputs | **702 / 1,538** rows have at least one NULL reliability input | Mostly historical provenance debt, but not exclusively: 143 of 958 conflicts created since 2026-10-02 have NULL `new_source_reliability`. |
| File-derived metadata conflicts | **261** `file_size`/`file_profile` + **6** `row_count` | Target for recomputed metadata is zero. For size/profile, 103 updates kept the existing value and 158 accepted the new one. |
| Forgotten slots | **32** total; **31** on non-tombstoned frames | Soft deletion may retain raw history, but **0** forgotten values should drive retrieval or ordinary topic search. |
| Generic-name frames from consolidation's own generic set | **14 live** | Prompt says never to create them; extraction only rejects blanks. Frame `entity` (#132) is an active conflict sink. |
| Frame vectors for configured model | **10,023 chunks across 5,835 frames; active primary coverage 5,775/5,775** | Coverage target met. Count-shape staleness query returns **0**. |
| Potential content-refresh lag | **140 / 4,231 live frames with slots = 3.31%** | Target zero after a top-up cycle. This is an upper-bound proxy because same-value reinforcement also restamps a slot; scheduled outputs and file examples below confirm real stale content cases. |
| Episode vectors | **5,564 rows; 5,562/5,562 current episodes covered; 2 orphans** | Current-episode coverage met; orphan target is zero. |
| Ownership | **5,200 shared + 575 user-owned live frames; 163 unowned frames carry 221 `user`/`user_correction` slots** | Only one user exists now, so no current cross-user leak was observed. Personal-write ownership fails the household invariant and must be fixed before a second account. |
| Summaries | **35 frames; 34/36 eligible live sessions covered; 6 behind episode count; 0 `summarizes` edges** | Coverage and freshness should be measured, not inferred from the six-hour timer. No summary-content conflicts is healthy. |
| Associations | **6,041 total; 478 touch a tombstone; 0 endpoint orphans** | Tombstoned endpoints are valid history, but graph APIs must not present them as live evidence. |
| Integrity | `quick_check = ok`; **57** orphaned history rows; **2** orphaned episode embeddings | `quick_check` passes; `PRAGMA foreign_key_check` does not. Do not conflate the two. |

## Ranked risks — architecture, lifecycle and fleet

### R1 — The consolidation plan merges distinct entities; the scheduler's cap is the only thing preventing damage

**Evidence.** The dry-run proposes, among 74 merges:

- `los_angeles <- los_angeles_rams` (city vs sports team);
- `jason_lee <- the_jason_lee_podcast` (person vs podcast);
- `michael_bosworth <- michael_bosworth_band` (person vs band);
- `central_texas <- central_texas_gardening` (place vs an aspect of it);
- `new_york_us <- new_york_city_office` (place vs office);
- `world_news <- bbc_news` and `world_news <- reuters_world_news` (topic vs publishers).

`consolidate.py` Pass 4 admits strict-subset names at Jaccard ≥ 0.6; `{los, angeles}` vs `{los, angeles, rams}` scores 2/3, and the four-word aspect denylist cannot generalize to “rams”, “podcast”, “gardening” or “office”. Survivor selection also has hollowing examples: #37 `indie_alternative_rock_band` has **0 slots** and is chosen to absorb frames with slots; #107 `world_news` also has **0 slots**. Reserved identity frames are absent from the plan, so the earlier reserved-name guard is working but insufficient.

LOG repeats `Consolidation skipped (74 merges > cap 10)` at every check, while cluster count rises 273 → 277. Because every dry-run plans the same first 50 clusters and produces 74 > 10, no bounded subset is applied and no backlog drains.

**Smallest fix.** Keep this plan unexecuted and do not raise the cap. Make the apply path select only merges with exact-normalized-name evidence, capped at 10 with the existing fresh-snapshot guard; keep embedding/token-overlap candidates in review-only dry-run until a labelled brain-copy precision test passes. Do not “fix” this by adding more aspect words or by weakening reserved-frame/title-url rules.

**Measure.** Pre-register pair precision on a labelled sample containing the six pairs above: bar **zero distinct-entity applications**, no reserved frame/tombstone mistakes, and a declining count of exact-name duplicate clusters over successive runs. The current plan fails the first bar.

### R2 — Recomputed file metadata goes through belief revision, so reinforced stale metadata defeats the current file

**Evidence.** `pipeline/files.py:948-1001` writes deterministic file shape (`file_size`, `file_profile`, `row_count`, `columns`, paths) through `upsert_slot`. These are recomputations, not claims from competing sources. RO counts **261** size/profile conflicts and six row-count conflicts.

Frame #6988 (`file_<redacted>`) alone has **102** such conflicts. Its current `file_size` is **1875**. Latest conflict #4869 records existing `1875`, new `5353`, resolved `1875`, with equal reliability 0.8 but existing confidence 0.65 vs new 0.5. The next profile conflict likewise keeps 42 lines / 148 words over the newly computed 52 lines / 876 words. The frame has been touched as recently as 2026-10-27, so this is not an abandoned file. The rename path already uses the correct distinction: `set_derived_slot` at `files.py:1123-1131`.

**Smallest fix.** In the single `apply_file_to_memory` path, write deterministic identity/profile/count fields with `set_derived_slot`; keep extracted entities and CSV cell assertions on `upsert_slot`. Re-sync existing file metadata only through the human-approved backup/brain-copy process, not as part of this audit.

**Measure.** Regression test: edit one file through three sizes/profiles and assert that the last bytes determine every metadata value and create **zero** new conflicts. In prod after an approved resync, `conflicts WHERE slot_key IN ('file_size','file_profile','row_count','columns')` must stop growing and #6988 must match the on-disk file.

### R4 — `upsert_slot` drops source-default reliability during revision, weakening the user's authority

**Evidence.** The store applies `default_source_reliability(source_type)` only on insert (`store.py:1401-1425`). On a differing-value update it passes raw `source_reliability=None` into `revise`, which defaults that side to 0.5, and a new winner is then saved with NULL reliability (`store.py:1491-1526`). Ordinary conversational extraction supplies `source_type="user"` but normally no explicit reliability.

RO finds **12 current `user` slots with NULL reliability**, including #132's latest `full_name`. Among conflicts since 2026-10-02, **143/958** have NULL new reliability; #132 has 18 such rows. The dedicated assistant-name exception is not implicated: `identity_name.full_name` currently has user reliability **1.0**, as designed.

**Smallest fix.** Compute one effective reliability at the start of `upsert_slot` (`explicit value, else default_source_reliability(source_type)`) and use that same value for revision inputs, conflict provenance and any slot update. Do not add a second comparator or change ladder order. Separately test same-value reinforcement so a user confirmation cannot silently reduce an existing reliability.

**Measure.** New regression cases for user-vs-search and user-vs-user revisions must record `new_source_reliability=0.95` and apply the documented rung. After deployment, no new `source_type='user'` revision should leave NULL reliability; historical NULLs remain history, not something to fabricate.

### R7 — The embedding top-up checks vector count, not content freshness

**Evidence.** `embed_stale_frames` compares chunk count with a slot-count formula. It returns **0** stale frames, yet the timestamp query finds **140** live frames whose latest slot update is newer than their latest vector. Example #6988: latest slot conflict 2026-10-27, vector updated 2026-10-18. Five scheduled-task frames also have 2026-10-26 result/run slots against 2026-10-25 vectors. `#7243 Austin News`, `#5275 contact_entity_alerts`, `#3891 garden_planting_prep`, `#2540 job_postings_monitor`, and `#542 ai_news_briefing` all appear in that result. These outputs change without necessarily changing slot count, so the current check is blind to them.

**Smallest fix.** Extend the existing top-up's grouped SQL to treat `max(slots.updated_at) > max(frame_embeddings.updated_at)` as refresh-needed as well as count mismatch. Keep the same model, batch cap and non-destructive timer; do not start a re-embed migration.

**Measure.** Use the new `embedding_refresh_lag_frames` metric below: after one completed top-up, genuinely changed frames should be refreshed and the lag count should fall toward zero (same-value reinforcements may make the proxy conservative). Verify a current scheduled result can be retrieved by a term absent from the prior result.

### R6 — Personal-memory ownership is not end-to-end; it is unsafe to add another household account first

**Evidence.** RO: **5,200/5,775** live frames are shared, and **163 shared frames hold 221 user/user-correction slots**. Thirty-five of those frames were created since 2026-10-20, so this is not only legacy data. `user_identity` itself is unowned; the three `user_content` frames are also unowned.

`store_turn_memory` knows no user id; `resolve_or_create_frame` has no owner argument and exact-name lookup returns any owner's frame before `create_frame` can apply its owner namespace. The tool writer behaves differently and assigns an owner on creation. On the read side, BrainPage scopes only `getFrames(actingUser())`; it fetches `/memory/associations`, `/memory/conflicts` and `/memory/search` without an acting user. `/memory/search` explicitly calls `search_similar_frames(user_id=None)`. The extra `user_id=1` used for this audit is ignored by that route and by `/memory/conflicts`.

Only `users = [[1, "Michael"]]` exists now, so **no present cross-user disclosure is claimed**. The defect is the invariant and the next-account failure mode.

**Smallest fix.** Thread the acting owner through conversational extraction, user-content registration and canonical lookup; an exact/normalized match must be owner-compatible before reuse. Require the acting user on observatory search/conflicts/associations and filter them to that owner plus deliberately shared frames. Do not backfill ownership by guessing: classify existing rows on a brain copy with human review.

**Measure.** A two-user integration test must show that same-named frames cannot be read or written across accounts and that search, conflicts and associations return no other-owner rows. On the live single-user brain, track the 163 unowned user-fact frames separately rather than hiding them under a guessed owner.

### R8 — Bare generic frames are still accepted and become many-subject, single-key conflict sinks

**Evidence.** The extraction prompt forbids names such as `entity`, `book` and `artist`, but `_drop_degenerate` rejects only blank names/keys. RO finds **14** live names from the consolidation generic set. Frame #132, literally `entity`, has **13 slots and 23 conflicts** spanning unrelated subjects. At 2026-10-27 00:50:19, three sequential conflicts changed its one `full_name` slot from four distinct names in sequence (values redacted). `UNIQUE(frame_id,key)` then turns distinct entities' names into apparent contradictions and the recency tiebreak keeps whichever came last.

**Smallest fix.** Enforce the existing “specific proper noun/title” boundary before frame resolution: route bare generic names to the dropped/review path using one shared generic-name definition. Keep the confidence ladder unchanged. Existing generic frames need human correction; rejecting new ones does not license an automatic cleanup.

**Measure.** New extraction turns produce zero new generic-name frames and zero new writes to #132. Audit conflict creation per generic frame before/after; it should stop rising from that channel. Do not infer that the historical 23 rows were all genuine disagreements.

### R9 — Foreign-key audit invariants are already breached even though the DB passes `quick_check`

**Evidence.** RO: **57** `slot_history` rows lack a parent slot and/or frame; two `episode_embeddings` rows (episode ids 2756/2757) lack episodes. `PRAGMA foreign_key_check` returns these violations; `PRAGMA quick_check` returns `ok`. Forty orphan history rows concern `open_questions` on frame #1186; other groups concern removed file previews and file metadata. `MemoryStore._open` enables foreign keys, but the shared `open_checked_db` used by other writers does not do so centrally.

**Smallest fix.** Enable `PRAGMA foreign_keys=ON` in the shared checked-connection helper used for writes, preserving the explicit OFF switch only for controlled import/export. Audit remaining direct `aiosqlite` writers. Preserve the historical orphan rows until a human-approved restore/cleanup decides their provenance; do not delete them in this audit.

**Measure.** Add a deletion/cascade regression test and run `PRAGMA foreign_key_check` before and after maintenance. New orphan count must remain zero; the historical 57 + two are a separate, explicitly measured backlog.

### R11 — Summarization revisits the newest five sessions instead of prioritizing missing/stale summaries

**Evidence.** `_run_summarization` takes sessions in last-activity order and processes the first five eligible sessions every six hours, whether or not they already have a current summary. RO: 36 eligible sessions have at least 10 episodes; only 34 have a live summary frame. Missing are `conv_0eab65bf7f14` (22 episodes) and `conv_394d0527669f` (39). Six existing summaries lag their session, including #1823 at 16 recorded turns vs 62 episodes. LOG shows the same newest sessions being updated every cycle. Zero summary-content conflicts confirms the derived-slot split is working.

**Smallest fix.** Within the existing five-session cap, order eligible sessions by missing summary first, then oldest `date_end`/largest turn lag, and skip summaries already current. Do not add another summarization model or inject a duplicate summary section; `conversation_recall` already falsified the latter.

**Measure.** Coverage should reach **36/36** eligible sessions without increasing the per-run cap; stale inactive sessions should converge to zero, while the active session may lag by at most one normal interval plus its new turns. Summary-content conflicts must remain zero.

## HCI findings — inspect, correct, forget and undo

### R3 — Soft deletion is not respected consistently by search and inspection

**Evidence.** RO: 32 priority-0 slots, 31 on live frames. `retrieval._frame_block` correctly skips them in the prompt, but `_frame_to_embed_chunks` embeds them, `search_frames_lexical` has no slot-priority filter, and topic-search responses serialize the full slot list. A live query `GET /memory/search?q=<redacted>+Iowa&user_id=1` returns #13 `mozworth` at 0.95 with `origin` and `origin_city` whose priorities are both **0**. Separately, `/memory/frames?user_id=1` returns all **71** tombstones; `list_frames` neither filters `deleted_at` nor selects it, so the response's default `deleted_at=null` cannot warn the UI. BrainPage maps those rows as ordinary nodes.

**Smallest fix.** Define active-read filtering at the search/inspection boundary: exclude priority-0 slots from lexical evidence and topic-match slot lists, exclude them from newly generated vectors, and omit or explicitly label tombstoned frames in list responses. Keep raw getters for export/history/consolidation so soft deletion remains auditable rather than becoming a hard delete.

**Measure.** Repeat the <redacted place> query: no priority-0 slot may be returned or supply a 0.95 lexical match, while raw history still contains the 32 rows. `/memory/frames` must contain zero unlabelled tombstones. Prompt tests must retain the existing healthy priority-0 skip.

### R5 — The trace reports candidate memory, not the memory left after the prompt budget

**Evidence.** Retriever sets `memory_context.formatted`. `_assemble_prompt` makes a separate budgeted string, and `_fit_prompt_to_cap` may make another smaller one, but neither updates `memory_context.formatted`. `MetaEvent` later emits that original field at `orchestrator.py:2342`; the footer and answer-confidence inputs also use selected frames rather than rendered frames. LOG records **10** `Refitted memory section ... to fit the prompt cap` events and one `System prompt truncated from 20146 to 12000 chars`. Therefore the TracePanel can show memory that was removed before generation.

**Smallest fix.** Store the actual final rendering back onto `memory_context.formatted` after initial budgeting and after refit, and use the rendered frame set for user-facing “used memory” claims. Do not persist more prompt text per episode to solve this; fix the live meta first.

**Measure.** Force a regression turn where one known frame is dropped by refit: that frame must be absent from `meta.memory_context` and from any “answered from N facts” claim, while still present in a separate candidate/debug count if desired. Existing TracePanel tests should distinguish candidates from final memory.

### R10 — The conflict interface has per-frame actions but no usable review queue or auto-decision history

**Evidence.** API: **275 pending** rows, all with NULL `resolved_value`, newest dated 2026-10-01. BrainPage filters to pending and shows them only after the user finds/selects the affected frame; there is no global review list. The **1,263 auto-resolved** rows (463 existing-wins) disappear from BrainPage and, after the live “What I learned” turn, have no historical review/undo surface. Of pending rows, 164 currently equal existing, 31 new and 80 neither; bulk reclassification from those current values would be unsafe.

**Smallest fix.** Add a read-only chronological conflict review view with pending/auto filters and frame links, reusing the existing correction/manual-override action for a deliberate new decision. Do not auto-close the legacy 275 and do not build an LLM conflict ranker.

**Measure.** Every pending and auto conflict is reachable without graph hunting, and a user can issue a new correction from one. Track explicit human resolutions over time; the backlog should fall because it was reviewed, not because statuses were rewritten.

## Observability — logs and the one metric to add

Available-window log evidence:

- **33** `context_fixed` turns: five turns dropped tool results, **59** dropped results total; one generation-failure shape logged `window=0`.
- **32** `context_usage` turns: maximum **99.9%** (32,719/32,768), three at ≥90%, **zero** `truncated=True`. This is evidence the aggregate tool budget is load-bearing; do not remove it.
- **10** memory refits and **one** hard system-prompt truncation. Current logs do not say how many selected memory frames survived, which is why R5 needs a regression assertion rather than another vague “quality” log.
- **Zero** startup/embedding failure or warning lines matched. Top-up logged 1–3 frame re-indexes on its successful cycles.
- Three scheduled backup successes in the available window and no backup failure line matched. The backup directory lists five scheduled snapshots plus five older consolidation snapshots; newest scheduled file is about 157 MiB.

### Add `embedding_refresh_lag_frames`

**Definition.** Count live frames whose newest slot `updated_at` is newer than the newest vector for the configured embedding model. It is a refresh-lag proxy, not a claim that every same-value reinforcement changed meaning. It directly exposes the blind spot in R7: today's count-shape check reports **0**, while this query reports **140**.

```sql
WITH s AS (
  SELECT frame_id, MAX(updated_at) AS latest
  FROM slots GROUP BY frame_id
), e AS (
  SELECT frame_id, MAX(updated_at) AS latest
  FROM frame_embeddings
  WHERE embedding_model = :configured_model
  GROUP BY frame_id
)
SELECT COUNT(*) AS live_frames_with_slots,
       SUM(s.latest > e.latest) AS embedding_refresh_lag_frames
FROM frames f
JOIN s ON s.frame_id = f.id
LEFT JOIN e ON e.frame_id = f.id
WHERE f.deleted_at IS NULL AND f.priority > 0;
-- Audit result: 4231, 140
```

Log this count and the number refreshed on each existing top-up. Alert only if genuinely changed frames remain after a completed cycle; do not page on the conservative proxy merely because a same-value reinforcement restamped a slot.

## Healthy — do not change

- **The confidence ladder remains the single comparator.** All **1,263** auto rows are decisions with winner/time (463 existing, 800 new), and the assistant-name exception is intact at reliability 1.0. `conflict_ladder_value` closed the model-comparator line; R4 is propagation of the existing ladder's inputs, not a new ladder.
- **Derived summaries no longer manufacture conflicts.** RO finds **zero** conflicts for `summary`, `key_entities`, and `open_questions`. Preserve `set_derived_slot` for computed values; R2 applies that same distinction to files rather than removing it.
- **The merge safety guards that work should stay.** The dry-run contains no `user_identity`/`identity_name` merge, reserved system types are excluded, merge slots are designed to go through `upsert_slot` with observation time preserved, and the >10 cap prevented the unsafe 74-merge plan from being applied. Do not relax these controls to make the backlog look smaller.
- **Embedding model identity and dimensions agree.** `/health` and metadata name `qwen3-embedding:0.6b`; all 10,023 frame chunks and 5,564 episode vectors are 4,096-byte blobs (1,024 float32 dimensions). Active primary-frame coverage is **100%**, current-episode coverage is **100%**, and there were no embedding warnings in the available logs. R7 is freshness, not a model migration.
- **Association-bearing empty frames are not automatically garbage.** Of 1,544 slotless frames, **1,539 have edges**. The measured graph walk improves recall (0.667 → 0.803 in `walk_value`), and `max_graph_frames=20` produced byte-identical delivered prompts to 7. Do not bulk-prune these nodes or raise the graph cap.
- **The prompt-side forget guard and owner-scoped episode reads work.** `_frame_block` skips priority-0 slots; chat history, session restore and semantic episode recall pass concrete owner ids. R3 is about the other read paths, not a reason to delete those correct filters.
- **Temporal handling should not grow another validity schema.** Every slot has `updated_at`; the renderer adds `as of` dates. The measured router gate refreshes period questions, while prior slot-validity machinery was removed for missing the episode-carried failure. Do not reintroduce it.
- **The context budget is doing real work.** There were 59 bounded tool-result drops on five turns and zero `truncated=True` results in 32 measured turns, with a 99.9% maximum. Keep aggregate bounding and actionable drop markers; address transparency separately.
- **Backups are fresh and independent of merge frequency.** Scheduled snapshots continue on the 12-hour clock despite consolidation being skipped. Portable export/restore code uses a merged SQLite backup rather than a raw WAL-unsafe copy; its live restorability remains unverified here, not something to assume or to test on prod.
