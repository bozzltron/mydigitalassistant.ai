# Tech Debt

Tracked from the 2026-09-28 frontend cleanup (47 SolidJS lint warnings, six real
bugs, plus the last two JSX `<style>` blocks). Every entry below is a live,
measured condition — not a style preference. Each entry says how to know it is
resolved; most resolve by deleting an allowlist entry in
`frontend/src/styles/designSystem.test.ts` or a cell in the table here.

Related docs: `docs/TODO.md` (memory/pipeline audit debt) and
`frontend/INLINE_STYLE_AUDIT.md` (how these bugs were found).

## Frontend: inline styles invisible to lint (27 remaining)

The `solid/style-prop` lint rule cannot enforce the AGENTS.md ban on inline
styles. It only matches camelCase keys (`fontSize`), so any `style={{...}}`
written with kebab-case string keys (`"font-size"`) or a dynamic value is never
reported. After clearing the 47 warnings, **27 inline styles remain** — eslint
reports zero:

| File | Count | Nature |
|---|---|---|
| `components/brain/BrainPage.tsx` | 9 | 5 legend dots use undefined vars (see next entry), 1 static kebab, 3 dynamic (mode toggle, `TYPE_COLORS` lookup, confidence `%` width) |
| `components/brain/BrainGraph.tsx` | 5 | 3 static kebab, 2 dynamic (legend line colour, tooltip x/y) |
| `components/ui/TopBar.tsx` | 4 | 3 static kebab, 1 dynamic display toggle |
| `components/brain/FrameDetail.tsx` | 3 | 1 dynamic `%` width, 1 dynamic type colour, 1 static |
| `components/chat/ChatPage.tsx` | 2 | 1 static kebab, 1 static `display:none` |
| `components/chat/InputBar.tsx` | 1 | static `display:none` |
| `components/chat/TracePanel.tsx` | 1 | dynamic display toggle |
| `components/Home.tsx` | 1 | static kebab |
| `components/brain/BrainGraph3D.tsx` | 1 | static kebab |

**Fix:** move static ones to the matching domain stylesheet; the genuinely
dynamic ones (widths from signals, tooltip coordinates) become CSS custom
properties or class list toggles.
**Resolved when:** the `style={{` row in the count above is empty and
`designSystem.test.ts` can drop its per-file migrated list.

## CSS custom properties with no definition (6)

A custom property with no definition and no fallback makes the whole declaration
invalid at computed-value time — the property silently computes to its initial
value. Six are in use; `designSystem.test.ts` allowlists them and the test fails
on any **new** one:

| Token | Used by | Effect today |
|---|---|---|
| `--surface3` | `styles/chat.css:982` | trace panel background declaration is dead, so that background is transparent |
| `--person` `--concept` `--event` `--household` `--entity` | `BrainPage.tsx` legend dots | the five Brain Observatory legend dots render with **no fill** |

Root cause: `variables.css` has a semantic alias layer (`--color-border:
var(--border)` and ten siblings) that is only half built. The same family of bug
previously made the EditModal Save button white-text-on-no-background and killed
34 declarations in AlertsPanel/TrashCan; those five aliases were completed/moved
this session. These six are the residue, hiding in the *brain* domain.

**Fix:** either define the tokens in `variables.css` (legend dots need a real
colour decision — the palette has `--accent`, `--accent2`, `--error`,
`--warning` but no per-entity colours) or remap the usages to existing tokens.
**Resolved when:** the `KNOWN_UNDEFINED` allowlist in `designSystem.test.ts` is
empty.

## YouTube playback is broken, and the working code is "dead" (decision needed)

Backend `pipeline/search.py` detects YouTube URLs and emits a video id +
thumbnail (lines 408-425, 529-549). `frontend/src/utils/media.ts` maps them to
`type: 'youtube'`. `MediaCard` renders the poster placeholder — but **nothing
reads its `data-embed-url`**, and the card's click handler only opens the
lightbox for `type === 'image'`. So clicking a YouTube result does nothing.

The only working embed implementation in the tree is
`frontend/src/components/chat/VideoEmbed.tsx` (renders a real `<iframe>` with
click-to-load), which has **zero callers** — it was never wired in. It was
corrected to match the new rules (reactivity + styles) rather than deleted,
because deleting it removes the only working player.

**Options (a feature decision, not a cleanup):**
1. Wire `VideoEmbed` (or an iframe swap in `MediaCard`) into the youtube branch
   of the message renderer.
2. Accept that YouTube links open in a new tab and make the poster a real link
   instead of a dead play button.
3. Delete `VideoEmbed` and the `type: 'youtube'` branch — removing the dead end
   entirely (then remove the youtube tests in `mediaReactivity.test.tsx`).

**Resolved when:** a choice is made; either YouTube plays in-app, the poster
links out, or the branch is gone with the tests.

## Lint scope config gap (`eslint .` vs `eslint src`)

`npm run lint` is `eslint src` and passes (0 errors, 0 warnings). `npx eslint .`
reports **4 parsing errors** in `eslint.config.js`, `test-setup.ts`,
`vite.config.ts`, and `vitest.config.ts` — because they are not inside
`parserOptions.project`'s tsconfig. CI is unaffected, and widening the glob to
the whole repo is **not** the point of this entry (lint still can't see
kebab-case inline styles — see first entry).

**Fix:** move the four root config files under a project that eslint's
`parserOptions.project` covers (e.g. a `tsconfig.eslint.json`).
**Resolved when:** `npx eslint .` exits 0.

## Two sources of truth for the frontend build

The dev flow serves source live: Caddy routes everything except `/assets/*` and
API paths to the solid-dev-server, which mounts `frontend/src` and compiles on
request. The *committed* production bundle lives in
`assistant/backend/static/assets/*` (rebuilt by `vite build`, baked into the
Docker image, served by Caddy only for `/assets/*`). Keeping the committed
bundle in sync is a manual step on every frontend change.

Two consequences: (1) the image must be rebuilt to ship a bundle, but the
running app actually serves the dev sources — so the baked bundle is near-
vestigial in daily use; (2) stale bundles accumulate in git if the rebuild is
forgotten (this session removed two generations in one commit).

**Fix:** decide whether the committed bundle is the deployment artifact (then
stop mounting `src` live in the dev compose service) or whether the dev server
is the deployment (then stop committing `assistant/backend/static/assets`).
**Resolved when:** one of the two artifacts is authoritative and the other is
gitignored.

## Orphaned Docker volumes (3)

`assistant-data`, `assistant_assistant-data` and `mydigitalassistant.ai_assistant-data`
(predates the volume-name fix) are orphaned from earlier compose project names;
the live volume is `mydigitalassistantai_assistant-data`. Left in place
deliberately — they may hold a pre-rename copy of the brain. Verify contents
against a current backup before removal (`assistant db backup` first).
**Resolved when:** each old volume is either confirmed junk and removed, or
archived to brain-backups.