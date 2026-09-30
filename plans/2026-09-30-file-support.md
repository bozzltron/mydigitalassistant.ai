---
date: 2026-09-30
status: active
estimated_hours: 8
---

# Plan E — File support: the agent's own artifacts

## Objective

Make the sandbox filesystem genuinely usable so the agent can write durable artifacts for
the user — the daily journal being the motivating case — and read them back.

Same *kind* of thing as an alert (Plan C): an artifact the agent produces rather than a
message it sends. It is separate because the failure is a separate subsystem.

## Measured state

The gaps are visible in the live brain, not inferred:

```
task last_result_summary, frame 683:
  "I currently don't have any files available to access. Howeve..."
task last_result_summary, frames 1945/1946/1947/2156/2540/3359:
  "I've created a file called 'test.txt' with the content 'hello'"
```

Two symptoms:

1. **Tasks report no files available** while the sandbox root exists
   (`SANDBOX_ROOT = /app/data`, `filesystem.py:15`) and `list_files` is a working tool.
   Either the sandbox is genuinely empty, or listing and reading disagree about where files
   live.
2. **Repeated identical `test.txt` output** across six unrelated tasks suggests tasks are
   echoing a stale artifact rather than doing their own work — which is the "task
   hallucinating success" failure, not a file bug.

**Not yet established which of these is true.** Phase 1 is diagnosis, not implementation —
the same posture as the scheduler investigation, which found the mechanism sound and the
config off.

## Relationship to other plans

- Independent of A, B, C, D — nothing blocks on it except the daily-journal idea, which is a
  *use* of files rather than a dependency.
- **Shares a root with Plan C**: both are "the agent produces something for the user."
  If files land first, the journal is a natural first artifact; if alerts land first, an
  alert can point at a file. Neither blocks the other, and the overlap is intentional
  (same pattern, different medium) rather than duplication.

## Phases

### Phase 1 — Diagnose (~2 h)

1. Determine whether the sandbox is empty or whether `list_files` and `read_file` disagree
   about paths. `list_sandbox_files` and the read path both resolve against `SANDBOX_ROOT`;
   confirm they agree, and confirm what is actually on disk.
2. Determine whether the six `test.txt` summaries are stale echoes or real writes. Check
   whether a `test.txt` frame/artifact exists and when it was created.
3. Write the findings down before changing anything, as with the scheduler investigation.

**Acceptance:** a written diagnosis naming which symptom is real, with evidence.

### Phase 2 — Make write/read/list consistent (~3 h)

Driven by Phase 1. The likely surface:

1. One path resolution used by list, read, write, glob, and delete — no divergent roots.
2. File frames created on write and removed on delete, so memory and disk do not drift.
3. `read_file` works for both a sandbox relative path and an uploaded-file frame name (the
   tool already claims both; verify both).

**Acceptance:** write → list → read → delete round-trips; a file created by the agent is
findable by `glob` and readable by name.

### Phase 3 — The daily journal (~3 h)

The motivating feature, and the agent's own record of what it learned.

1. A scheduled task (the existing mechanism) that writes a dated journal file.
2. Journal content is model-written from the day's memory — not a template (principle: no
   templated responses).
3. The journal is an ordinary file, so it is retrievable, readable, and greppable like any
   other artifact.

**Acceptance:** a day's run produces a dated file with model-written content covering what
the agent learned; the file survives a restart.

## What this plan does not do

- **Does not build a file manager UI.** Existing `FilesPage`/`FileGrid`/`FileViewer` are
  not in scope unless Phase 1 shows they are broken.
- **Does not change the upload path** (`file_upload` frames) unless Phase 1 implicates it.

## Requirements

**Functional**

| # | Requirement | Phase |
|---|---|---|
| R1 | Determine whether the sandbox is empty, or whether list/read disagree about paths, with evidence recorded in writing. | 1 |
| R2 | Determine whether the repeated `test.txt` summaries are stale echoes or real writes. | 1 |
| R3 | List, read, write, glob, and delete resolve paths against one root. | 2 |
| R4 | File frames are created on write and removed on delete, so memory and disk do not drift. | 2 |
| R5 | `read_file` works by sandbox relative path **and** by uploaded-file frame name (the tool claims both). | 2 |
| R6 | A scheduled task writes a dated journal file with model-written content drawn from the day's memory. | 3 |
| R7 | The journal survives a restart and is retrievable/greppable like any file. | 3 |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | Diagnosis before implementation — write down what is actually broken before changing code. |
| N2 | No templated journal content. The model writes it from memory. |
| N3 | Files stay in the local sandbox (`/app/data`). Path traversal stays guarded (`filesystem.py:61-65`). |
| N4 | No new external calls. |

**Constraints**

- **Shares a root pattern with Plan C** — both are "the agent produces something for the
  user." That overlap is intentional (same pattern, different medium), not duplication.
- **Independent** — nothing blocks on it except the journal *use case*.

## Dependencies and ordering

- **No blockers.** Can run in parallel with any other plan.
- **Shares a root pattern with Plan C** (agent-produced artifacts) but neither depends on
  the other.
- Within the plan: 1 → 2 → 3. Phase 3 is a *use* of files and must follow Phase 2.

## Test strategy

- `test_sandbox_paths_consistent.py` — list/read/write/glob/delete resolve to the same root.
- `test_file_roundtrip.py` — write → list → read → delete.
- `test_read_file_by_frame_name.py` — an uploaded file is readable by frame name.
- `test_journal_written_daily.py` — a run produces a dated journal file with real content.

Gates: `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py`,
then the full suite, then `ruff check .`.

## Principle alignment

| Principle | How |
|---|---|
| No templated responses | Journal content is model-written from memory. |
| Scheduled tasks are memory | The journal run is an event; the file is the artifact. |
| Clean ship | Diagnosis before implementation; nothing added until the real defect is known. |
| Stability | Round-trip tests for the filesystem path. |
| Safety & Privacy | Files stay in the local sandbox (`/app/data`); path traversal is already guarded in `filesystem.py:61-65`. |

## Rollback

Phase 2 changes path resolution — revert with `git revert`. Phase 3 is an additive scheduled
task, disabled by its own setting. No schema migration.
