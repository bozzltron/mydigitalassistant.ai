---
date: 2026-10-02
status: active
estimated_hours: 8
---

# Plan H — Code review: conformance to the project's own principles

## Objective

An audit of the codebase against `AGENTS.md` and `assistant/AGENTS.md` found seven
conformance findings. Fix them. The security hard rules, the critical-path tests,
the data-driven experiment discipline, and the no-templated-responses rule all
pass and are not touched here.

## Measured findings (evidence, not inference)

**F1 — literal inline styles in `brainLib.ts` (corrected during implementation).**
The first pass flagged every Brain JSX `style={{...}}` (`BrainPage.tsx`,
`FrameDetail.tsx`, `BrainGraph.tsx`) as a violation. That was wrong:
`designSystem.test.ts` explicitly **sanctions** `style={{ '--custom-prop': v }}`
as the way to pass a dynamic value to CSS, so those were already conformant — and
the class rewrite that followed duplicated the palette and made the confidence bar
lossy, so it was reverted. The genuine defect is `brainLib.ts`, which builds the
tooltip as an HTML string and set literal `style="color:…"` there — invisible to
the guard, which only scanned `.tsx` files.

**F2 — Dependency lists drifted, and the declared one is incomplete.**
`pyproject.toml` `[project].dependencies` omits `sqlcipher3`, `cryptography`,
`faster-whisper` and `python-multipart` — all real runtime dependencies. So the
documented dev install (`pip install -e ".[dev]"`) produces an install without
SQLCipher: no encrypted brain. `assistant/pyproject.toml` is a second, stale copy
(a different dependency set and `package-dir`); it is **not dead** — pytest uses
it as the `configfile` for `assistant/tests/`, and it is the only place the
`learning_exam` marker is declared. The `Dockerfile` hand-maintains the real list
in two stages.

**F3 — Emoji used as UI icons.** `AGENTS.md`: "Use SVG icons only (no emoji)."
`FileGrid.tsx:81-87` and `FileViewer.tsx:54-62` return emoji as file-type icons;
`FrameDetail.tsx:123,180` uses `⬢`; `Modal.tsx:49` / `EditModal.tsx:53` use `✕`
while the lightbox close already uses an inline SVG.

**F4 — Stale build artifacts tracked in git.** 15 files under
`assistant/backend/static/`. None contain `topbar-btn`, `VideoGallery`, `MicIcon`
or "Open external link?" — they predate the current source. `.gitignore` covers
`dist/` but not this output directory. The Dockerfile rebuilds it; the committed
copy only serves the dev `/assets/*` mount.

**F5 — Dead CSS.** Verified unreferenced in any non-test source: `.btn`,
`.user-badge`, `.voice-bubble` (+ `h3`/`.processing`/`.speaking`), `.pulse-ring`
(+ `::after`), `.voice-actions`, `.voice-stop`, `.voice-cancel`. The `.toast-*`
rules flagged by the same scan are live (applied via a template string).

**F6 — `.env.example` is missing `CONFLICT_AUTO_RESOLVE`.** Every other key in
`.env` is documented.

**F7 — Three experiments have a plan and no result.** `daily_run_retrieval_magnet`,
`daily_schedule_e2e`, `memory_health`. The project's rule is plan-before-data,
result-after-verification; nothing distinguishes "in flight" from "abandoned."

## Requirements

**Functional**

| # | Requirement |
|---|---|
| R1 | The tooltip HTML emits no literal inline style property (custom properties are the sanctioned form); the design-system guard scans `.ts` as well as `.tsx`. |
| R2 | `pyproject.toml` declares every runtime dependency, so `pip install -e ".[dev]"` yields a working (encrypted-capable) install. |
| R3 | The Dockerfile installs from the declared dependencies rather than a hand-maintained second list. |
| R4 | The duplicate `assistant/pyproject.toml` is resolved: its unique content (the `learning_exam` marker) is preserved and pytest still finds a config for `assistant/tests/`. |
| R5 | No emoji is used as a UI icon; file-type glyphs and the modal close are SVG. |
| R6 | Stale build artifacts are untracked and gitignored; the build still produces them. |
| R7 | Dead CSS is removed. |
| R8 | `.env.example` documents `CONFLICT_AUTO_RESOLVE`. |
| R9 | Each of the three plan-only experiments is either completed (with `result.md`) or marked clearly in its `plan.md`. |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | No new external calls, cloud APIs, or telemetry. |
| N2 | No behavior change to the chat/memory hot path. |
| N3 | Every fix carries a regression test where one is meaningful (R1/R5 escapable via a source lint if cleaner). |
| N4 | Clean ship: removals are verified zero-caller before deletion. |
| N5 | The full backend and frontend suites stay green. |

## Design

- **F1:** the tooltip name's colour becomes a custom property
  (`style="--type-color:…"`) read by `.tooltip-name`, and the fixed tokens
  (`--text-dim`, `--warning`, `--error`) become `muted` / `essential` / `conflict`
  classes. The design-system guard is extended to read `.ts` files, since the JSX
  rule never saw the string form.
- **F2/R4:** root `pyproject.toml` becomes the single source of truth (add the
  four missing runtime deps). **Confirmed:** pytest resolves
  `configfile: pyproject.toml` with `rootdir: /app/assistant` for
  `assistant/tests/`, i.e. it is using the *nested* file. So `learning_exam` and
  `asyncio_mode` must be added to the root `[tool.pytest.ini_options]` before the
  duplicate is removed, or a full-suite run changes behaviour.
- **F3:** add file-type and close icons to a shared icon module (`ui/Icons.tsx`)
  and use them in `FileGrid`, `FileViewer`, `FrameDetail`, `Modal`, `EditModal`.
- **F4:** `git rm --cached assistant/backend/static/assets assistant/backend/static/index.html`
  plus `.gitignore` entries. The SVGs stay tracked: Vite cannot empty a directory
  outside its own root, so they are hand-maintained and served at runtime.
- **F6:** document the key.

## Dependencies and ordering

1. **F2/R4 first** — the dependency/declaration fix is the one affecting whether a
   fresh checkout runs at all.
2. **F5, F6, F4** — mechanical clean-ship, independent.
3. **F1, F3** — the UI-principle fixes; F1 and F3 both touch Brain components, so
   sequence them together.
4. **F7** — documentation state, last.

## Test strategy

- `pytest` full suite, both plain and encrypted, plus `ruff check .`.
- `npm run check` (lint + typecheck + tests).
- A source guard test asserting no `style={{` in `frontend/src/components/**`
  (R1), and no emoji in `frontend/src` non-test files (R5) — the two rules most
  likely to regress.
- `docker build` succeeds with the Dockerfile derived from `pyproject.toml`
  (R3).

## Principle alignment

| Principle | How |
|---|---|
| Clean ship | F4/F5 remove stale artifacts and dead CSS; removals are caller-verified. |
| Design tokens / UI standards | F1 moves theming to classes; F3 uses inline SVG. |
| Stability | A failing install (F2) is itself the strongest stability fix. |
| Data driven | F7 makes an experiment's state legible rather than ambiguous. |

## Rollback

All changes are source-only and revertable with `git revert`. Untracking build
artifacts (F4) does not delete the working files.

## Resolved while implementing

- **F1 (corrected):** the JSX custom-property inline styles were sanctioned, not a
  defect. Only `brainLib.ts`'s literal styles were real, and the guard now covers
  `.ts`.
- **F2/R4 (resolved):** pytest used `assistant/pyproject.toml` as `configfile`
  (`rootdir: /app/assistant`). `asyncio_mode`, `pythonpath`, `testpaths` and the
  `learning_exam` marker moved into the root config, the duplicate was deleted, and
  two `from scripts.…` imports were corrected to `assistant.scripts.…` (the nested
  rootdir had made the bare path resolvable).
- **F4 (scoped):** only `assets/` and `index.html` are generated; the SVGs are
  runtime assets Vite leaves in place, so they stay tracked.
- **F7 (resolved):** `daily_schedule_e2e` is superseded; `memory_health` is a tool.
  Both now say so in their plan. `daily_run_retrieval_magnet` already stated it is
  pre-registered with no data.
- **Not in scope:** the documented open gaps — answer text not streamed
  (`TextDeltaEvent` unconstructed), portable-brain export/import unwired, and the
  Modal's missing focus trap. These are known and deliberate; they are recorded in
  `AGENTS.md`, not defects.
