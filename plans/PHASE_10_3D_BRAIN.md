# Phase 10 — Deploy Consolidation & Search Fixes; 3D Brain Fly-Through

Status: planned
Date: 2026-08-24

## Context

Phase 9 consolidation ran live (M&W memories unified), twice-daily scheduling is
wired, search pipeline hardened, and Brain Observatory topic search fixed
(tokenized lexical matching + semantic/keyword blend). All committed through
`f2b589c`. **The running backend still serves the old baked image** — none of
this is live until rebuilt.

## 10.1 Deploy (do first)

1. `docker compose build assistant && docker compose up -d`
2. Verify: `/health` reports fresh heartbeat; scheduler log shows consolidation
   timer armed (`scheduler_enabled=True`, interval 12h).
3. Smoke: `GET /memory/search?q=mountain wolf` → `mountain_in_the_wolf` (#276)
   must rank #1 at sim 0.95 (verified offline against live data pre-deploy).
4. Confirm backup ring exists after first scheduled run:
   `data/backups/brain-pre-consolidation-*` (WAL-checkpointed copies).

## 10.2 Review follow-ups (deferred, ordered)

From the code-review pass (H1/H2/M1/M2/M4/M6 fixed pre-commit):

- **M3**: `_apply_merge` spans several short-lived connections; make
  copy+redirect+tombstone one transaction (or per-merge cursor like the
  strengthen pass) so retries can't inflate confidence without evidence.
- **M5**: application-level lock (metadata lease row) so CLI consolidate,
  scheduled consolidation, and GC can't mutate concurrently.
- **L5**: rewrite episodes.frame_ids to survivors during merge so topic view
  shows merged history (the M&W surgical merge did this by hand; automate it).
- **L3**: bound `search_frames_lexical` candidate SQL once corpora grow.
- **T4**: pass-2/generic-name absorption tests exist for empty values only;
  add absorption-by-generic-name case.
- Cosmetic: SearXNG filler pattern hits hyphenated compounds ("hi-fi").

## 10.3 Brain Observatory 3D fly-through

Goal: Google-Earth-style navigable brain — smooth fly-to on topic select,
free orbit/zoom, depth-conveying layout. Extends existing `brain.html`
force-graph, no new dependencies beyond a WebGL renderer.

Design:

- **Renderer**: 3D force layout (frames = nodes in 3D space, associations =
  edges). Evaluate `3d-force-graph` (tiny, wraps three.js) vs hand-rolled
  three.js; prefer the former — it's static-vendored, no CDN at runtime
  (privacy rule: no external calls).
- **Camera**: cinematic fly-through — eased camera tween to a selected frame
  (rotate + dolly), inertia orbit on drag, scroll zoom, double-click to focus.
  "Tour" mode: slow auto-orbit drifting between high-confidence clusters.
- **Data**: existing `GET /memory/search` + graph endpoints; add
  `GET /memory/graph3d` returning nodes/links with cluster assignments and
  confidence → size/brightness mapping.
- **Detail panel**: unchanged (slots, associations, conflicts); selecting a
  node flies the camera instead of just highlighting.
- **Fallback**: keep current 2D canvas if WebGL unavailable.
- **Constraints**: SVG icons only, links open new tab, latest Firefox/Chromium.

Tasks:

1. Vendor `3d-force-graph` + `three.js` into `assistant/backend/static/vendor/`.
2. Add `/memory/graph3d` endpoint (nodes, links, clusters, confidence).
3. Rebuild brain.html view layer: 3D scene, fly-to tweens, tour mode toggle,
   keep conflict-resolution + search panels working against 3D selection.
4. Tests: API shape for graph3d; JS kept thin (logic in store/endpoint).

## Acceptance

- Deployed backend serves blended brain search (#276 top hit).
- Scheduled consolidation runs twice daily with WAL-safe backups.
- 3D brain renders locally, fly-to works on search/select, no network egress
  beyond loopback services.
