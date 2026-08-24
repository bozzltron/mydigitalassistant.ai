# Phase 9 — Memory Consolidation ("Dreaming") & Reflection

Post-Phase-8 audit of the live memory web (Mozworth session, read-only SQLCipher
inspection) found that connections exist but are structurally weak. This phase makes
association strength real, adds an offline consolidation pipeline ("sleep"), and adds
reflection (episodic→semantic promotion). Grounded in complementary learning systems
(CLS) theory and the 2025–2026 convergence: Letta sleep-time agents, Sleep-time Compute
(Lin et al. 2025), Anthropic Dreams. Anti-goal: over-engineering — we build the boring,
evidence-backed parts (dedup/merge/strengthen), not REM-style link generation.

## Verified findings (audit + code)

| ID | Evidence | Problem |
|----|----------|---------|
| F1 | schema.py:100 `UNIQUE(from,to,relation)`; extractor.py:318-329 | Re-extracted edges hit IntegrityError which is silently swallowed → association confidence frozen at exactly 0.5 forever (live DB: 115/115 edges at 0.5). AGENTS.md claims co-occurrence strengthening; no such code exists. |
| F2 | extractor.py:268-284; store.py:156-166 (`WHERE name = ?`) | Frame resolution is exact-name match only → near-duplicate frames proliferate (live: the album exists as `mountain_in_the_wolf`, `the_mountain_and_the_wolf`, `album_title`, `album`). Graph walks see only one fragment's neighborhood. |
| F3 | Live DB | Type-as-name junk frames: `entity`, `song`, `movie`, `book`, `concept` hold facts duplicated onto proper-noun frames. Hallucinated edges exist (`mozworth founded sam_altman`) with zero episode support. Grab-bag frames conflate unrelated facts (`location`: Earth + English coast + home studio). |
| F4 | gc.py:67, 106-120 | Slot decay requires priority < 0.5 but new slots start at 0.5 and nothing ever lowers priority except explicit negative feedback → decay branch is effectively dead; nothing auto-forgets in practice. Frame stale rule (priority<0.2 + 30d) equally unreachable. |
| F5 | episodes.frame_ids JSON array | Co-occurrence data needed for Hebbian strengthening already exists per-turn; nobody computes it. |

## Design principles

1. **Fast writer stays fast**: hot path gets only cheap upsert+bump and a similarity
   check; heavy work moves offline.
2. **Slow consolidator is mostly deterministic**: merge/strengthen/prune run on SQL +
   embeddings. LLM appears only in reflection (Phase C), gated hard.
3. **Nothing hard-deletes**: merges tombstone losers (deleted_at), pruned edges get
   soft-delete semantics; every mutation logs to slot_history or a consolidation report.
4. **Provenance survives**: reflections must cite episode IDs; uncorroborated reflections
   start below default confidence so they cannot dominate retrieval alone.
5. **Dry-run first**: every destructive pass supports dry_run and prints a report.

## Non-goals

- REM-style novel-association generation (hallucination industrializer).
- RL-trained consolidators (Auto-Dreamer); region-replacement semantics (destroys audit
  trail); any new graph/vector infra; cloud services (hard security rules).

## Tasks

### Phase A — make strength real (hot path)

- [x]A1 — `store.create_association`: catch IntegrityError internally → apply
      `bump_confidence` to the existing edge instead of raising. Update
      extractor.py:318-329 to stop blanket-swallowing (keep narrow guard). Add
      regression test: extract same edge twice → second call raises confidence above 0.5,
      row count stays 1.
- [x]A2 — Canonical frame resolution in `apply_extraction`: before `create_frame`,
      normalize the candidate name (lowercase, strip articles/punct, collapse spaces)
      and check exact-normalized match first. If none, embed the name and query
      `search_similar_frames(user_id=None, min_distance=0.10)`; reuse the top frame when
      distance < threshold AND types are compatible. New config:
      `canonical_name_distance` (default 0.10).
- [x]A3 — Extraction prompt guardrails (extractor.py both prompts): frame names MUST be
      proper nouns/titles; forbid bare type words (entity/song/movie/concept/thing);
      cap associations at 4 per extraction, most meaningful first; closed relation-type
      vocabulary hint list. Unit test on prompt text invariants.

### Phase B — Consolidation pass (offline, weekly)

- [ ]B1 — New module `memory/consolidate.py`: `run_consolidation(db_path, dry_run) ->
      ConsolidationReport`. Three passes, in order:
      (a) **Merge**: cluster frames by normalized-name equality OR embedding distance <
      `canonical_name_distance`; require compatible type. Survivor = highest
      confidence, earliest created. Union slots (conflict-safe upsert), redirect
      association endpoints (dedupe via A1 path), copy max confidence, delete loser's
      frame_embeddings rows, set loser deleted_at, log reason=`consolidation_merge` to
      slot_history.
      (b) **Association reconciliation**: for each edge, count episode co-occurrence of
      endpoints via `json_each(episodes.frame_ids)` (last N days). Co-occurring edges →
      bump once per run; edges with confidence == 0.5, zero lifetime support, age >
      `consolidation_edge_ttl_days` → soft-delete (priority=0). Kills hallucinated edges
      by evidence.
      (c) **Embedding refresh**: re-embed merged survivors.
- [ ]B2 — Wire weekly timer in scheduler/runner.py beside the GC ISO-week check
      (offset day so they don't collide). Report counts logged like GcReport.
- [ ]B3 — CLI: `assistant db consolidate [--dry-run]` mirroring `gc_db` (cli/db.py:220).
- [ ]B4 — Tests: merge unions slots and redirects edges; unsupported edge pruned after
      TTL; supported edge strengthened; dry_run writes nothing; encrypted-DB variant.

### Phase C — Reflection (episodic→semantic)

- [ ]C1 — Trigger: after chat turn, if user's episode count since last reflection ≥
      `reflection_every_episodes` (default 40) → enqueue reflection (run inside
      scheduler heartbeat tick, not request path). Store watermark in metadata table.
- [ ]C2 — `pipeline/reflector.py`: utility model reads recent episode digests → returns
      JSON insights [{statement, frame_names[], episode_ids[]}]. Validation: every
      cited episode must belong to the user; uncited → rejected; cap 3 insights/run.
      Stored via normal apply_extraction with source_type=`inference`,
      confidence=0.45 (below INITIAL_CONFIDENCE so corroboration is required to reach
      full weight), provenance marked in source_type.
- [ ]C3 — Tests: insight with fake citation rejected; valid insight stored at 0.45;
      trigger fires only at threshold; watermark advances.

### Phase D — forgetting that bites (tuning, last)

- [ ]D1 — After A–C produce real signals: lower GC exemption bar (decay slots whose
      priority < 0.5 OR not strengthened in DECAY_AFTER_DAYS regardless of initial
      0.5), using bump events from A1/B1 as reinforcement markers.
- [ ]D2 — Junk-frame heuristic: frames whose normalized name equals their type word
      flagged in consolidation report (manual review via Brain page; no auto-delete).

## Config additions (.env / config.py)

- `CANONICAL_NAME_DISTANCE` (default 0.10) — embed-distance gate for frame reuse.
- `CONSOLIDATION_EDGE_TTL_DAYS` (default 60) — unsupported-edge soft-delete age.
- `REFLECTION_EVERY_EPISODES` (default 40) — episodes between reflection runs.
- `REFLECTION_ENABLED` (default true) — kill switch.

## Enriched requirements (edge cases & constraints)

### R1 — Alias map prevents re-fragmentation [Phase B, schema]
After consolidation tombstones a duplicate, `get_frame_by_name(loser_name)` misses and
extraction would recreate it. New table `frame_aliases(alias_norm TEXT PRIMARY KEY,
frame_id INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE)`: consolidation
records loser→survivor mappings; A2 resolution checks aliases BEFORE similarity search.
Aliases are rewritten when a survivor is itself merged later (chase to final target).

### R2 — Owner isolation (privacy hard rule) [A2/B1]
Canonicalization and merging may only unify frames with identical `owner_user_id`
(NULL == NULL for shared household frames). Similarity candidate scans filter by owner;
consolidation clusters are owner-partitioned. A user-private fact must never absorb
into (or leak out of) a shared frame via merge.

### R3 — Merge safety rules [B1]
- Type compatibility groups: merge only within compatible types (e.g. song↔song_title,
  album↔album_title); `person ≠ album`. Incompatible → skip + log in report.
- Self-loop guard: after endpoint redirect, drop edges whose endpoints became equal
  (extractor.py:316 already does this at creation time).
- Relation-type collision: same pair with different relation_types stay separate rows;
  merging redirects per (pair, relation) using the A1 bump path.
- Slot key collisions on merge: keep higher-confidence value via existing
  `upsert_slot` revise path (never silently overwrite; conflicts table records it).

### R4 — Bounding & performance [B1/C]
- `max_merges_per_run` cap (default 50): consolidation is resumable; next run continues.
- Consolidation runs inside the scheduler tick (single-writer SQLite serializes vs chat
  writes naturally). No cross-process locking needed.
- Reflection capped at 3 insights/run, utility model only, one LLM call.

### R5 — Normalization definition [A2]
`normalize(name)` = lowercase, strip leading articles (the/a/an), remove punctuation,
collapse whitespace. Skip canonicalization for normalized names shorter than 4 chars
(avoids false merges of short proper nouns like "Bo"); exact-name match still applies.

### R6 — Rollout safety
- CLI `consolidate` defaults to dry-run; real run requires explicit flag.
- `REFLECTION_ENABLED=false` kill switch; reflection never runs on first install
  (watermark starts at current episode count).
- Every destructive pass logs before/after counts; GcReport-style dataclasses.

## Test matrix

| Test | Phase | Asserts |
|------|-------|---------|
| `test_association_bump_on_reextract` | A1 | Same edge extracted twice → conf > 0.5, count == 1 |
| `test_association_unique_no_duplicate_rows` | A1 | N extractions → 1 row, monotonically non-decreasing conf |
| `test_canonical_resolution_exact_normalized` | A2 | "The Mountain & The Wolf" ≡ "the mountain and the wolf" |
| `test_canonical_resolution_embedding_threshold` | A2 | Near-name reuses frame below distance gate |
| `test_canonical_respects_owner_isolation` | A2/R2 | Different owners → separate frames |
| `test_short_names_skip_fuzzy` | A2/R5 | "Bo" not merged into "Bob" |
| `test_prompt_guardrails_invariants` | A3 | Prompt forbids type-words, caps associations |
| `test_consolidation_merge_unions_slots` | B1 | Survivor has union; values preserved; history logged |
| `test_alias_prevents_recreation` | B1/R1 | Post-merge extraction by loser name lands on survivor |
| `test_unsupported_edge_pruned_after_ttl` | B1 | Zero-support old edge soft-deleted; supported survives |
| `test_dry_run_writes_nothing` | B/R6 | Fixture DB byte-identical after dry-run |
| `test_reflection_citation_validation` | C | Fake episode id rejected; valid stored at 0.45 |
| `test_encrypted_db_consolidation` | B | Runs under SQLCipher |

## Acceptance criteria

1. Extracting the same fact+edge across two sessions yields one frame, one edge,
   edge confidence > 0.5 (regression test proves F1 fixed).
2. "The Mountain & the Wolf" vs "the mountain and the wolf" resolve to one frame in
   apply_extraction unit test (F2 fixed).
3. `assistant db consolidate --dry-run` on a fixture DB with known duplicates reports
   planned merges without writing; real run reduces frame count and preserves all
   slot values reachable via the survivor.
4. An edge with zero episode support older than TTL is soft-deleted; the
   sam_altman-style edge class cannot survive two consecutive consolidation runs.
5. Owner isolation holds: no test or code path merges frames across owners.
6. Full suite green plain + SQLCipher (`./run_ci.sh`); ruff clean.
