# Plan: 3D Brain Observatory

## Status
Active — cleanup pass completed, awaiting validation in live environment.

## Goal
Give the Brain Observatory a compelling 3D view that communicates "memory as a living, navigable space" while keeping the existing 2D force-graph as the reliable fallback. The shape does **not** need to look like a biological brain; sphere, constellation, branching universe, or clustered nebula layouts are all acceptable as long as the dark space background is preserved.

## Why keep it
- Strong UX differentiator for a privacy-first AI assistant.
- The data model (frames/nodes, associations/links) maps naturally to force-directed graphs.
- The 2D fallback already works, so 3D can be an enhancement rather than a hard dependency.

## Current state
- 2D force-graph is stable.
- `3d-force-graph` v1.73.3 + Three.js r152.2 load correctly from CDN in the test page.
- Critical HTML bug discovered and fixed: the `<script>` tag for `3d-force-graph.min.js` was missing its closing `</script>`, which swallowed the following `<style>` block.
- Initialization pattern fixed from `new ForceGraph3D(el)` to the documented factory call `ForceGraph3D()(el)`.
- Debug logging removed; production-appropriate error logging remains.
- Space background restored in 3D mode via a generated `.starfield-3d` layer.
- Runtime fallback added: if the WebGL renderer does not append its `.scene-container` within 800 ms, the view automatically degrades to 2D.
- WebGL cleanup added on mode switch and page unload.
- **Search highlighting**: frames returned by topic memory search are now highlighted in both 2D and 3D views; non-matching nodes and links are dimmed.
- **Branch zoom**: clicking a search result or focusing a frame now fits the active node plus its immediate neighbours into view in 2D, and flies the 3D camera to the branch centroid.
- Clear highlight when the topic panel is closed or the search box is emptied.

## Open concerns
1. **CDN dependency** — `three.min.js` and `3d-force-graph.min.js` currently load from `unpkg.com`. For a privacy-first product this should be re-vendored locally.
2. **Renderer reliability** — the root cause of the earlier `childCount: 0` issue was the broken HTML parse, but we still need live validation.
3. **Mobile/battery** — WebGL force graphs are heavy; the default on low-power devices should remain 2D.
4. **Shape** — default force-graph is a free-floating 3D cloud. We may want a more intentional layout (sphere, clusters by frame type, etc.).

## Design options (ranked by implementation effort)

### 1. Free-floating 3D cloud (default)
- Use the default `3d-force-graph` force layout.
- Nodes colored by frame type, link opacity/confidence mapped.
- Lowest effort; already implemented.

### 2. Spherical / orbital layout
- Pin nodes to a large sphere surface or orbital shells by type.
- Requires a custom `dagMode` or manual positioning; moderate effort.
- Visually reads as a "planetarium of memory."

### 3. Clustered nebula / constellation
- Group nodes by frame type into clusters using `d3-force` clustering or `ngraph` constraints.
- Add subtle fog/dust effects via Three.js particles.
- Higher effort but very distinctive.

### 4. Branching universe / tree
- Lay out associations as a 3D tree using `dagMode`.
- Best for hierarchical relationships; may not suit associative/cyclic memory graphs.

**Recommendation**: ship option 1 first, then experiment with option 2 in a follow-up branch. Preserve the dark space background in all variants.

## Technical implementation
- File: `assistant/backend/static/brain.html`
- 3D container: `#brain-canvas-3d`
- Library: `3d-force-graph` v1.73.3 (bundles `three-render-objects` internally)
- Explicit Three.js load: `three@0.152.2` from CDN for global `THREE` availability
- Factory initialization: `graph3d = ForceGraph3D()(el)`
- Starfield: 200 small `div` elements generated per 3D initialization
- Cleanup: `dispose3D()` disposes TrackballControls/OrbitControls, calls `_destructor()`, clears DOM, nulls the reference

## Privacy / vendor strategy
1. **Short term**: load from CDN to unblock validation and design iteration.
2. **Before release**: re-vendor both files locally under `assistant/backend/static/vendor/`.
3. **Integrity**: add `integrity` attributes or pin exact versions.
4. **No telemetry**: ensure no library calls home (these libraries are static; they do not).

## Testing strategy
- Unit: not applicable (pure frontend).
- Manual smoke tests:
  1. Open `/brain-ui` on Chromium and Firefox.
  2. Confirm 3D graph renders with nodes and links.
  3. Click a node — tooltip appears.
  4. Toggle 2D/3D — no console errors, 2D graph re-renders.
  5. Resize window — 3D canvas resizes correctly.
  6. Disable WebGL (via browser flags) — page falls back to 2D automatically.
  7. Leave page open, switch modes repeatedly — no WebGL context exhaustion.
  8. Run a topic memory search — matching frames and their neighbours highlight in 2D/3D, non-matching elements dim.
  9. Click a search result — camera/view zooms to the node plus its immediate branch.
  10. Close topic panel or clear search — highlight clears.
- Regression: run `pytest assistant/tests/` to ensure backend endpoints still pass. Smoke subset: 93 passed (test_api, test_extractor, test_belief_revision).

## Decision
**Keep the 3D feature.** The failures were integration bugs, not fundamental problems. With the HTML parse bug fixed, the factory pattern corrected, cleanup added, and a 2D fallback in place, the risk is low and the payoff is high.

## Next steps
1. Deploy the current cleanup and verify 3D renders in the live environment.
2. If rendering works, add a volume mount in `docker-compose.yml` for `assistant/backend/static` to speed up frontend iteration.
3. Re-vendor `three.min.js` and `3d-force-graph.min.js` locally and remove CDN dependency.
4. Decide on a long-term layout (sphere/nebula/tree) and prototype it.
5. Add an explicit 2D/3D preference in settings or localStorage (already partially implemented).
6. Consider defaulting to 2D on mobile/touch devices.

## Risks and mitigations
| Risk | Mitigation |
|------|------------|
| WebGL unavailable or unstable | Automatic fallback to 2D |
| CDN outage / privacy concern | Re-vendor locally before release |
| Performance on low-end hardware | Default to 2D, make 3D opt-in |
| Browser-specific rendering bugs | Test on Chromium and Firefox; keep 2D fallback |

## References
- `assistant/backend/static/brain.html` — main implementation
- `assistant/backend/static/vendor/` — intended local vendor location
- `assistant/tests/test_review_fixes.py` — backend regression tests
- `assistant/AGENTS.md` — UI/UX standards
