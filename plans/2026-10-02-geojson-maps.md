---
date: 2026-10-02
status: active
estimated_hours: 15
---

# Maps — a GeoJSON file in the sandbox, rendered on the Earth

> **Revised 2026-10-13: scope reduced to four requirements.** The earlier draft let
> the *agent* author coordinates, which brought a provenance gate, two tools, a
> named-map switcher, a slug-collision problem, and a Phase 0 experiment. None of
> that is needed if the agent only **reads** the map. This is the simple version.

## Scope

1. A map page.
2. The user clicks the map and adds a point with a description — and can edit or
   remove one.
3. It saves as a GeoJSON file in the user's sandbox.
4. The agent can reference the map by **reading that file** in conversation.

**No experiment.** The project's rule is to measure a non-trivial belief before
building on it. The earlier draft's one such belief was *"does the model author
coordinates correctly?"* — out of scope now that the agent only reads. Everything
that remains is plumbing over features that already exist (a sandbox file, MapLibre
rendering GeoJSON, `read_file`), so there is nothing to pre-register.

## Not in scope

- **No agent tools for writing points.** The agent reads the map; it never
  originates a coordinate. That removes the provenance gate, the coordinate
  cross-check, and the Phase 0 experiment the earlier draft needed.
- **No named maps / switcher.** One map, one file. (A `POST /maps` + slug function
  was for "create and load different maps" — dropped.)
- **No geocoding, POI search, routing, terrain, or multi-user sharing.**
- **No upload-path change.** A `.geojson` upload is not required; the page is the
  only writer.

## Architecture

> **A map is a GeoJSON file in the sandbox. The page writes it; the agent reads it.**

- **One file:** `/app/data/map.geojson` (the sandbox root — host-visible, see
  `docs/FILES.md`). It is an ordinary sandbox file, so **every existing file tool
  already reaches it** — `read_file("map.geojson")`, `glob("*.geojson")` — with no
  maps-specific code. Requirement 4 is satisfied by construction.
- **The DB never holds the contents.** The earlier draft's scar, kept: a stored copy
  can be served *instead of* the file when a disk read fails. The points live in the
  file, full stop.
- **The write is atomic** through `write_sandbox_bytes` (`filesystem.py:165`) — the
  existing tmp+rename writer with the traversal/symlink guard.
- **No frame required.** The page reads and writes the file by its fixed path; the
  agent finds it with `list_files` / `glob("*.geojson")` and reads it with
  `read_file("map.geojson")`. Registering a frame via `apply_file_to_memory` would
  put it on the Files page and in retrieval, but that step runs extraction — an LLM
  call on every point added. Not worth it for the four requirements.

## The file

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [-122.68, 45.523] },
      "properties": { "description": "gate code 4412" }
    }
  ]
}
```

- **Coordinates are `[lon, lat]`.** GeoJSON is longitude-first. The most likely bug
  in the feature — a transposition is a *valid* point in the ocean and nothing
  errors. Validate at the boundary: `|lat| ≤ 90`, `|lon| ≤ 180`, **not both zero**
  (Gulf of Guinea — always a swap or a placeholder), and **both present**. Round to
  **6 dp** (~0.11 m) on write.
- **No `crs`** — RFC 7946 forbids it; WGS 84 assumed.
- **Properties with no value are omitted**, never `null`.
- **Indent 2, trailing newline.** The user may open this in a text editor, and the
  agent reads it as text.

## Backend — two routes and a settings key

| Route | Purpose |
|---|---|
| `GET /map` | the file's text; an empty `FeatureCollection` when the file is absent |
| `PUT /map` | body = a GeoJSON FeatureCollection → **validate → canonicalise → write atomically** → return it |
| `GET /settings` | **extended** — `maps_enabled`, `map_tiles_base_url`, `map_styles` |

**The page owns the whole file; the server validates and writes it.** The client
loads the map once, edits it in memory, and PUTs the whole FeatureCollection on
every change (add / edit description / remove). One endpoint, no merge logic, no
read-modify-write race — safe because **the page is the only writer** (the agent
only reads). The earlier draft's per-point endpoints and per-path lock existed for
agent+click concurrency; there is none now.

A small `maps/geojson.py` — **pure functions, no I/O**: `validate_fc` (shape/range on
load, so a corrupt file raises rather than being silently rewritten) and
`dumps` (indent 2, trailing newline, rounded coords, no null properties). `PUT`
runs both; `GET` returns the raw text so a hand-edited file surfaces honestly as
*"this isn't a valid map"* rather than being fixed up.

`maps_enabled` ships **false** and gates the TopBar button, the route, and tile
rendering together (see *Privacy*).

## Frontend

### MapLibre GL JS v6, directly

BSD-3-Clause. Chosen over **Leaflet** (DOM/CSS markers; MapLibre draws every point in
one GeoJSON source and one `circle` layer) and Mapbox GL (not OSS).

**No maintained Solid wrapper.** `solid-maplibre` is 0.5.0 (2025-03-09), MIT,
`peerDependencies: { "maplibre-gl": "*", "solid-js": "*" }`, no release in the 19
months since — a peer range of `"*"` is the absence of a compatibility claim, and two
MapLibre majors have shipped underneath it. `maplibre-gl` is **6.14.0** and **removed
the default export** (verified: `export default` is gone; `Map` and `setWorkerUrl` are
named exports). So `import { Map, setWorkerUrl } from 'maplibre-gl'`, and
`setWorkerUrl` is mandatory under Vite (`maplibre-gl-worker.mjs?worker&url`). **~150
lines of thin Solid layer.**

`maplibre-gl/dist/maplibre-gl.css` must be imported from **inside** the lazily
imported module so Vite code-splits it — a global import ships ~100 KB of map CSS to
the chat page. Verify in the bundle diff, not by assumption.

The **Brain graph** is the only component here that mounts a large WebGL library into
Solid, so it has already paid for the mistakes such a component invites. Six rules,
none about graphs:

| Rule | Precedent |
|---|---|
| Props are **accessors**, never destructured | `BrainGraph3D.tsx` |
| Every prop read in a library callback is `untrack()`ed | `BrainGraph3D.tsx` |
| Mutable local `let map` + one readiness signal | `BrainGraph3D.tsx` |
| Init gated on real layout dimensions (rAF on `offsetWidth`) | `BrainGraph3D.tsx` |
| Teardown in **both** `onCleanup` and the init `catch` | `BrainGraph3D.tsx` |
| Capability gate with graceful fallback | `BrainGraph3D.tsx` — but it checks WebGL **1**; MapLibre v6 needs **WebGL2**, so copy the shape, not the constant |

### `MapCanvas.tsx`

Lazy-loaded. **Exactly one Map instance, for the page's lifetime** — a WebGL context
is scarce (~8–16/page) and permanently held, so tearing one down per change is the
wrong reflex.

- **`addPointLayers` on initial load AND on `style.load`, idempotent.** `setStyle()`
  replaces the whole style, so a source and layers added with `addSource`/`addLayer`
  are gone and every point vanishes on the first style switch. One function owns the
  point source and its layers.
- **One GeoJSON source.** `cluster: true`, `clusterMaxZoom` ≈ 10; expand via
  `getClusterExpansionZoom`.
- **Click to add.** A `＋ Add point` toggle puts the canvas in add mode; the next
  click converts screen → `lngLat` and opens a small form for the **description**.
  Confirm → the point is appended to the in-memory FeatureCollection → `PUT /map` →
  the source updates. A mode toggle, not a bare click, so panning never drops a stray
  point. The screen → `[lon, lat]` conversion lives in one helper so the axis order
  is written once.
- **A point with no description renders a dot and no label** — no placeholder, no
  invented title. Clicking a point opens a detail panel: description, coordinates,
  edit, remove.
- **`fitBounds` on load and when the point set changes shape.** Adding a point
  re-frames the map; that is the whole interaction.
- **WebGL2 absent → a readable list of points, not a blank box.**
- `ResizeObserver` on the container — new to the codebase; existing views use
  `window.addEventListener('resize')` (`BrainPage.tsx`).

### `MapPage.tsx`

One canvas, full width; a header with the style picker and the add toggle. Reuse the
existing layout classes from `files.css`; `map.css` holds map-only selectors and must
not redefine a class `files.css` already defines (`designSystem.test.ts` fails on a
class defined in two stylesheets).

- `routes.tsx` — `/maps`. A `MapIcon` in `components/ui/TopBarIcons.tsx` (a
  folded-map glyph, `currentColor`, no emoji/icon font) and a TopBar anchor beside
  Files and Brain, rendered only when `/settings` reports `maps_enabled`.
- **Style picker** over `map_styles` — a native `<select>` (five options is not a
  toolbar). Choosing one calls `setStyle` and **must not re-frame**.
- **Pitch control** beside it, enabled when the active style has `supports3d`
  (0–60°, `maxPitch: 60`). Untoggling resets `pitch` to `0`.
- **Download GeoJSON** — a blob from the in-memory FeatureCollection; no endpoint.
- Style choice and camera live in `localStorage` (user preferences, not data).
- **Feedback on every action** (AGENTS.md: show feedback on user actions). Confirming
  a point shows a saving state, then a brief confirmation; a failed `PUT` shows the
  error and keeps the point in the panel so the work is not lost. The add-mode toggle,
  the panel and the picker use CSS transitions (~0.2s), never abrupt.
- **One writer, last-write-wins.** Two tabs editing the same map do not merge — the
  page owns the file and the agent only reads, so there is no concurrent-writer race
  to solve for a single-user household map.

**Four existing assertions change, in two tests** — the part that gets half-done:
`TopBar.test.tsx` gains `Maps` in the order label and `7 → 8` in the two length
assertions. New order: `Alerts, Let's talk, Files, Maps, Brain, Trace, Settings,
Trash`.

## Basemap: OpenFreeMap

No API key, no account, self-hostable. Styles MIT; map data OpenStreetMap (ODbL).

```
https://tiles.openfreemap.org/styles/{liberty|bright|positron|dark|fiord}
```

**`liberty` is the default** (its README: the OpenMapTiles styles are abandoned
upstream; Liberty is fresh). Five styles, not six — `GET /styles/3d` returns **404**;
the "3D" on the homepage is a demo, not a style. Liberty carries 3D buildings
(`fill-extrusion` over `render_height`); the others do not, which is what
`supports3d` records. **No DEM anywhere**, so `terrain`/`sky`/`fog`/globe stay off.

**No tile proxy — deliberate.** MapLibre is handed the style URL and fetches tiles
itself; the browser's HTTP cache is the right thing to make panning fast, and a
Python hop per tile would make it worse. This departs from the `/image-proxy`
precedent (`main.py:896`) on purpose: OpenFreeMap needs no key and a tile request
carries a viewport, not what the user said. **If the house prefers the invariant,
say so now and the proxy becomes a small extra phase.**

`MAP_TILES_BASE_URL` reaches the browser via `/settings`; it is our own `.env`, never
user input, so passing it to a client is not an SSRF vector.

## Privacy

**Enabling maps is the consent act.** `MAPS_ENABLED` ships **false** and gates the
TopBar button, the route, and tile rendering together. Turning maps on is the user
saying "fetch a basemap from OpenFreeMap." A dot on a map is a location; a basemap
under it turns that into a map. Point `MAP_TILES_BASE_URL` at a self-hosted
OpenFreeMap to remove even that. `docs/MAPS.md` states this in plain words.

MapLibre renders attribution from the style — **do not suppress it** (ODbL is a
licence condition).

## Phases

| Phase | Hours |
|---|---|
| 1 — Backend: `GET`/`PUT /map`, `geojson.py`, `/settings` | 3 |
| 2 — `MapCanvas`: MapLibre, click-to-add, style/pitch | 7 |
| 3 — `MapPage`, TopBar, `map.css`, route | 3 |
| 4 — Ship: docs, `.env.example`, both Compose projects | 2 |

## Test strategy

- **`test_maps.py`** — `PUT /map` writes and `GET /map` round-trips; a transposed /
  half / out-of-range coordinate is **refused** (the assertion that matters: a swap
  is valid GeoJSON, so only an explicit `[lon, lat]` check catches it); `42` and
  `42.000000` canonicalise to one value; a non-`Point` geometry is refused; an
  unwritable path leaves the original intact (atomicity); `GET` on an absent file
  returns an empty FeatureCollection.
- **`mapLib.test.ts`** — the pure maths: screen → `[lon, lat]`, bounds, `42` vs
  `42.000000`. No jsdom, no WebGL — this file covers the geometry.
- **`MapCanvas.test.tsx`** — import smoke test, a DOM mount assertion, and the
  WebGL2-absent list fallback. jsdom cannot initialise MapLibre, so canvas behaviour
  is proven by hand — **say that in the file** so thin tests are not mistaken for
  coverage.
- **`MapPage.test.tsx`** — `fetchMock` routing; the TopBar assertion updates.

Gates before merge: `ruff check .`, `pytest assistant/tests/`, `npm run lint`,
`npm run typecheck`, `npm run test`, plus `test_daily_schedule.py` +
`test_review_fixes.py`.

## Non-goals

- **Agent-authored coordinates** (the earlier draft's tools + provenance gate).
- **Named maps, a switcher, slug collisions.**
- **Trip planning, live position feeds, lines/polygons, a spatial index, POI search,
  directions, geocoding.**
- **Semantic recall over points** — the agent reads the file.
- **A tile proxy** — deliberate, argued above, reversible as one phase.

## Rollback

| Change | Reversal |
|---|---|
| `GET`/`PUT /map` | Delete the two routes; the file is still a normal sandbox file. |
| `maps_enabled` / `map_tiles_base_url` / `map_styles` | Keys in `config.py`; off leaves the page unreachable. |
| `map.geojson` | An ordinary file — still listed by `glob`, readable by `read_file`. Back up first. |
| Frontend | Delete `MapCanvas`, `MapPage`, `MapIcon`, the `routes.tsx` line, the TopBar anchor, `map.css` and its import; revert the four TopBar assertions. |
| `maplibre-gl` | One `package.json` line. |
