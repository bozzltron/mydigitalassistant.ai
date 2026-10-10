---
date: 2026-10-02
status: active
estimated_hours: 42
---

# Maps — a GeoJSON file, rendered on the Earth

## Requirements

1. Map page. 2. TopBar button. 3. Create and load different maps, like chat loads
conversations. 4. Persisted as GeoJSON files in the sandbox. 5. Creating a map is
creating a GeoJSON file. 6. Loading a map is reading one and rendering it. 7. Rendered
in SolidJS. 8. A map of the Earth. 9. Points are GeoJSON features. 10. Show name and
description where present. 11. Render every point. 12. The agent can create, update
and delete them. 13. The user says what they want; the agent does the rest. 14. The user
can add a point by clicking the map.

## Changes since this plan was written (2026-10-02 → 2026-10-10)

The plan was written against the codebase of 2026-10-02. Several things it assumed
have since moved, and one phase is now partly done. Read this before the sections
below; the line numbers cited throughout are from 2026-10-02 and have drifted.

**The sandbox gained a bytes writer.** Phase 1 proposed widening
`write_sandbox_file` to `str | bytes`. That work exists: `write_sandbox_bytes`
(`filesystem.py:165`) is the atomic bytes writer, and `write_sandbox_file`
(`filesystem.py:199`) now delegates to it. So the upload-path fix is "call
`write_sandbox_bytes`", not "add a bytes branch".

**The upload path still bypasses the sandbox helpers.** `upload_file_to_memory`
(`main.py:2268-2279`) writes with a bare `open(path, "wb")` against a hardcoded
`Path("/app/data")` — no atomicity, no traversal guard. Unchanged since planning,
and still the one gap worth closing for every upload, not just `.geojson`.

**The Files page is now an editor.** It gained, after this plan was written:
rename (`PATCH /files/{frame_id}`), in-place text editing
(`PUT /files/{frame_id}/content`), sort by last updated, PDFs opening in a new tab
(`GET /files/{frame_id}/download?inline=true`), a deep link (`/files?file=<id>` —
the chat links a filename mention to it), a "Back to chat" button, and a highlight
on the row of the file shown in the preview. The map page reuses the Files page
layout, so these are the patterns to copy — and `FilesPage.tsx` is no longer the
file the plan's line numbers point at.

**The file tools are richer.** Since planning the agent also has `rename_file`,
`edit_file` (line-addressed), `append_file`, and `search_file`, alongside
`write_file` / `read_file` / `delete_file` / `glob`. `docs/FILES.md` is the contract
for all of them; `docs/FILES.md` also documents the rename/save routes and the
shared folder below.

**A host-visible shared folder exists.** `/app/data/shared` is bind-mounted to the
host (`docs/FILES.md`), so a user can drop a `.geojson` in from their machine and
the agent reads it, or edit a map file in a text editor. It is inside the sandbox,
so it needs no special handling — but a map dropped in has **no memory frame**
until something writes it through `apply_file_to_memory`.

**The escalation changed.** The reasoner no longer auto-escalates a "multi-step"
query to `MAX_MODEL`; it keys on an explicit reasoning cue
(`assistant/experiments/escalation_trigger/`). A map turn with a file recipe
("read the map and add a point") stays on the warm model.

**Current anchors for the line numbers below:** `GET /files/list` `main.py:2372`,
`GET /files/{frame_id}` `main.py:2536`, `DELETE /files/{frame_id}` `main.py:2640`,
`GET /settings` `main.py:413`, `SUPPORTED_UPLOAD_EXTS` `files.py:35`,
`extract_file_content` `files.py:459`.

## Architecture

> **A map is a `.geojson` file, handled the way this app already handles every other
> file. The frame records what the file *is*; the contents live in the file.**

No map store, no `map` frame type, no `maps/` directory, no reconciliation, no
migration. A map *is* a file with `file_ext == "geojson"`.

### The database never holds a map's contents

Not a preference — a scar. `main.py:2227-2232`:

```python
# Memory records what the file *is*; the content stays on disk and is read
# verbatim. The preview used to be stored here and excluded at render time,
# which left a stale copy that could be served in place of the real file when
# a disk read failed.
```

A stored copy was served *instead of* the file, silently. The fix was to stop storing
it. Pinned by a test in Phase 1.

### GeoJSON is what MapLibre eats

`map.addSource(id, { type: "geojson", data })` takes the FeatureCollection
**verbatim** — no adapter, no re-projection, no conversion layer. That is the whole
reason this architecture works: the file format and the renderer's input format are
the same format.

Only an unknown geometry *type* throws, and we only ever write `Point`. A `crs` member
is ignored entirely, and we don't write one. Every feature carries both coordinates, so
the renderer never has to skip a half-built point.

### What already exists

| Need | Comes from |
|---|---|
| List maps | `GET /files/list` (`main.py:2372`), filtered to `name.endsWith(".geojson")` |
| Load one map | `GET /files/{frame_id}` (`main.py:2536`) → `.content` is the raw file text |
| Delete a map | `DELETE /files/{frame_id}` (`main.py:2640`) → `prune_file_frame` |
| Tile + style config | `GET /settings` (`main.py:413`) — already exists for the Brave toggle |
| Page layout | `FilesPage.tsx` — `.files-content` → `.files-main` + `<aside>` (line numbers moved; see the changes note above) |
| Agent deletes a map | existing `delete_file` tool |
| Atomic write, traversal + symlink guard | `write_sandbox_bytes` (`filesystem.py:165`) |
| A user drops a map in | `/app/data/shared` (`docs/FILES.md`) — host-visible, read by path |
| It appears in the Files page | already works |

**Do not add a `/maps` index.** `_file_response` exists only because
`/files/{id}/content` was permanently shadowed by a byte-identical `/files/{id}`
duplicate — *"a containment fix applied to only one of them would have been no fix at
all."* A fourth route to the same file is how that repeats.

**One caveat.** The *upload* path does not use the sandbox helpers:
`upload_file_to_memory` writes with a bare `open(path, "wb")` (`main.py:2279`) against
a hardcoded `Path("/app/data")` (`main.py:2268`) — no atomicity, no traversal guard.
Agent writes go through the sandbox writers and are fine; user drag-and-drop is not.
**`write_sandbox_bytes` (`filesystem.py:165`) already exists** — the atomic bytes
writer with the traversal/symlink check — so Phase 1 no longer *widens*
`write_sandbox_file`; it points the upload path at
`write_sandbox_bytes(..., overwrite=True)`. That closes the gap for every upload, not
just `.geojson`.

Adding `geojson` to `SUPPORTED_UPLOAD_EXTS` (`files.py:35`) is the only strictly
required change — `extract_file_content`'s dispatch (`files.py:459-475`) already
falls back to a plain-text decode. Add a dispatch entry anyway: a GeoJSON-aware
extractor can report the feature count and surface place names as entities, which is
what makes an uploaded map worth talking about.

## The file

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [-122.68, 45.523] },
      "properties": { "name": "North shed", "description": "gate code 4412" }
    }
  ]
}
```

- **No `crs`** — RFC 7946 forbids it; WGS 84 assumed.
- **No foreign members.** The human name lives in `file_name` and the filename.
- **Every feature has both coordinates.** `"geometry": null` is legal RFC 7946 and the
  renderer would skip it, but nothing writes one: a point is born from a map click or
  from coordinates the user typed, and both always produce a `[lon, lat]`.
- **Properties with no value are omitted**, never `null`. Half the points on a
  click-first map have no name; `{"description": null}` is noise.
- Indent 2, trailing newline. The user may open this in a text editor.

### Coordinates

**GeoJSON is longitude-first: `[lon, lat]`.** The most likely bug in the feature — a
transposition is a *valid* point in the ocean and nothing errors.

1. **Validate at every boundary** — the tool *and* the click endpoint: `|lat| ≤ 90`,
   `|lon| ≤ 180`, **never both zero** (Gulf of Guinea; always a swap or a
   placeholder), and **both present** — a half-coordinate is refused, not stored.
2. **Round to 6 dp on write** (~0.11 m). Dedup compares rounded values, so `42` and
   `42.000000` are one point.
3. **Phase 0 measures it.** Transposition costs the most and is invisible to a human
   reviewer.

### Correction semantics

Without this, every correction becomes a duplicate. `save_map_points` matches an
incoming point against existing features and **updates on match**:

1. **Incoming `name` matches a feature's `name`** → replace its coordinate and
   description. *The correction path.* A point the user has named has that name as its
   identity within the file.
2. **Otherwise coordinates match** (at 6 dp) → update its fields. *The dedup path.*
3. Otherwise append.

So `save_map_points("properties", [{name: "North shed", lat: 45.524}])` after
`45.523` was stored **moves** the point. Feature count unchanged, no tombstone — the
file is the truth and it now says 45.524.

Rule 1 needs a name, so the prompt tells the model to pass one when correcting a named
point. A bare coordinate is dedup-or-append: right for *"and 45.6, -122.7"*, wrong for
a correction. Name is therefore the identity: adding a second point with the same name
moves the first. That is the intended correction path, and duplicate-name collisions
are out of scope.

## Provenance: the model may never originate a coordinate

This gate guards the **agent path only** — a coordinate the user typed in chat. The
click path needs no gate: the pointer is the user, and the number it produces is
user-originated by construction.

**A gate, not a record.** The confidence ladder ranked two competing claims about one
value. A GeoJSON file holds one value per coordinate — nothing to rank. The only real
risk is *before* the write. So enforcement is a predicate at the boundary and the
number is discarded. **No provenance row is written anywhere.**

This matters *more* here than in a frames design: a wrong slot is one row with a score
the system will eventually challenge. A wrong file looks like ground truth and renders
confidently forever.

1. Canonicalise every numeric pair in the user's text to 6 dp. **Extract, don't
   substring-match** — `"45.523,-122.68"`, `"45.523 -122.68"` and `"N 45.523"` are one
   coordinate, and a naive test rejects the primary use case.
2. Look back this turn **plus five previous user turns** (`SessionMessage`,
   `main.py:1269`) so *"put those three on the survey map"* resolves.
3. **Fail closed.** If the pair appears in neither place the write is refused, with an
   error telling the model to ask. There is no geocoder: an address is not a coordinate,
   and the fix is to ask for one or point the user at the map.

Outside the window the check refuses and the model asks. Correct conservative failure.
*The check rejects; the prompt instruction prevents it.*

## Backend

`assistant/backend/maps/` — `config.py`, `geojson.py`, `store.py`.

**The backend only ever *writes* GeoJSON. It never serves parsed maps** — the client
reads the raw file from `/files/{id}`. So:

- `config.py` — `MAP_STYLES`, the `supports3d` table.
- `geojson.py` — **write-side only**, pure functions, no I/O: `canonical_coords`
  (validate + round, both coordinates required), `validate_fc` (shape/range on load, so
  a corrupt file raises rather than being silently rewritten), `merge_features` (the
  correction rules), `dumps` (indent 2, trailing newline, no null properties).
- `store.py` — `canonical_slug`, `resolve_map`, `merge_points`. **No `list_maps`, no
  `load_map`** — `/files/list` and `/files/{id}` do both.

Read-side work (`JSON.parse`, bounds, cluster input) is TypeScript. That split is
deliberate: the backend does what the model must be stopped from getting wrong, and the
client draws what the backend has no reason to see twice.

- `resolve_map(name, owner_user_id)` — resolve-or-create the file frame by
  `file_{slug}.geojson`, mirroring `extractor.resolve_or_create_frame`
  (`pipeline/extractor.py:303`). **Do this first** — name resolution *is* the switcher,
  and six one-point maps is a failed feature.
- `merge_points(frame_id, points)` — read, match-or-append, write atomically.
  **Read-modify-write in one function**, never split across rounds. It takes a
  per-canonical-path `asyncio.Lock` (an in-process dict of locks) so two concurrent
  writers — the agent and a map click, or two tabs — cannot lose an update. The write
  itself is already atomic (`write_sandbox_file`'s tmp+rename); the lock is what makes
  the *read-modify-write* atomic.

Both the agent tools and the click endpoint call `merge_points`, so there is exactly
one place that mutates a map.

### One slug function, or two maps per name

The sharpest edge in the design. The two creation paths sanitise differently:

| Path | Sanitiser | `"Properties"` becomes |
|---|---|---|
| Agent tool | `[^a-z0-9]+`→`-`, lowercased | frame `file_properties.geojson` |
| User upload | `[^a-zA-Z0-9_.-]`→`_`, **case preserved** (`main.py:2190`) | frame `file_Properties.geojson` |

Same map, two files, two frames — and *"put it on the properties map"* resolves to
whichever `get_frame_by_name` finds first. That is the *"six one-point maps"* failure
arriving through a door nobody was watching. Three things follow, all required:

1. **One `canonical_slug()`, used by every path that can create a map** — the tools,
   `POST /maps`, and the **`.geojson` upload branch only**. Where an uploaded `.geojson`
   canonicalises differently, **rename it on disk** rather than accepting a second
   identity. Non-`.geojson` uploads keep today's sanitiser: only maps resolve by name,
   so only maps need a single identity.
2. **Resolution is case-insensitive** — `get_frame_by_name` is exact, so try
   `file_{slug}.geojson` then fall back to a case-insensitive match over
   `file_%.geojson`. Same consolidation treatment users already get.
3. **`execute_delete_file` derives the frame as `f"file_{Path(path).name}"`
   (`tool_executor.py:1320`)** — so any filename the tools write must map back to the
   frame they resolved, or deleting a map orphans its frame and `prune_file_frame` never
   runs. This constrains the slug; it is not optional bookkeeping.

### Tools — two

```python
class SaveMapPointIn(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)

class SaveMapPointsArgs(BaseModel):
    map_name: str = Field(..., max_length=200)
    points: list[SaveMapPointIn] = Field(..., min_length=1, max_length=50)

class RemoveMapPointSelector(BaseModel):
    # A selector, not a point: name or a coordinate, but not nothing.
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    name: str | None = Field(None, max_length=200)

class RemoveMapPointsArgs(BaseModel):
    map_name: str = Field(..., max_length=200)
    points: list[RemoveMapPointSelector] = Field(..., min_length=1, max_length=50)
```

A save point requires **both** coordinates; a removal selector requires **at least one**
of name / lat+lon, and a half-coordinate is invalid in both.

`save_map_points` creates the file when absent (req 5), merges, writes atomically.
`remove_map_points` **refuses an empty selector** — `{}` would match everything.

**Both take a map *name*, never a path.** Letting the model supply
`maps/../../../etc/passwd` would defeat the guard `filesystem.py` already provides.

**Why two.** The tool list is in every prompt — one new tool is noise, five is a real
per-turn cost. **Why batched:** the tool loop's round ceiling is `max_tool_rounds = 6`
(`config.py:132`; `max_tool_rounds_deep = 12` for an escalated turn — it was a
`MAX_TOOL_ROUNDS = 3` constant when this plan was written) and `stream_tool_loop` runs
calls sequentially, so *"add these twenty coordinates"* cannot be spread over rounds.

Deleting a whole map needs **no tool** — `delete_file` exists and the file pipeline
drops the frame. Req 12 satisfied.

Wiring: arg models in `tools.py`, schemas from `builtin_tools()` (`:444`),
registration in `_register_builtin_tools` (`tool_executor.py:100-117`), timeouts in
`TOOL_TIMEOUTS` (`:144`), and both kept **out of** `_READ_ONLY_TOOLS` (`:1611`)
because they write.

**The gate needs the user's text, and there is one loop.**

Prerequisite (a separate change, not this plan): drop the non-streaming tool loop.
`run_tool_loop` (`tools.py:616`) is reached only from `orchestrator.chat()`
(`orchestrator.py:1450`), which serves only `POST /chat` (`main.py:634`) and the CLI
(`cli/app.py:180`); the frontend already uses `/chat/stream` exclusively. Removing
`POST /chat`, `orchestrator.chat()`, `run_tool_loop`, `execute_tools_parallel`,
`async_tools.py` and their tests leaves `stream_tool_loop` as the single call site.

Then the current turn's message reaches the map handler via a `ContextVar[str]` set
immediately before `execute_tool` in `stream_tool_loop` (an explicit `ToolContext`
object is the alternative if the handler signatures are worth churning). The handler
reads the `ContextVar` and the store (via `session_id`) for the prior five turns.
`execute_tool`'s dispatch signature `func(validated, user_id, session_id)`
(`tool_executor.py:1663`) is unchanged. This is what makes the refusal rule
enforceable — a prompt instruction is not a security boundary.

Prompt wording: *"Use when the user gives coordinates to put on a map, and name the
map they said. Copy coordinates from the user's own words exactly — never supply one
the user did not give. If the user gave an address or a place instead of coordinates,
do not guess: tell them to give coordinates or add the point by clicking the map. When
correcting a point that has a name, pass that name so the point moves instead of being
duplicated. If the phrasing could match more than one map, ask which. Do not use for
directions, distances, or weather."*

### Bounds

Antimeridian-aware, and **in TypeScript** — the client has the features, so computing a
rectangle on the server would ship parsed geometry over the wire to derive one number.
~15 lines in `mapLib.ts`.

A point at 122.7°W and one at 135.7°E give min/max bounds that wrap the wrong way and
frame the whole planet — silently wrong, not an error. Split on the largest gap between
consecutive longitudes, treat it as the seam, wrap only if points fall on both sides.

A one-point map returns `null` bounds and the page uses its saved camera. One asset in
one industrial park is a normal first run.

## The basemap: OpenFreeMap

No API key, no account, no signup, self-hostable. Styles are MIT; map data is
OpenStreetMap (ODbL).

```
https://tiles.openfreemap.org/styles/{liberty|bright|positron|dark|fiord}
```

**Five, not six.** `hyperknot/openfreemap-styles` documents exactly these five, and
`GET /styles/3d` returns **404** — the "3D" on the homepage is an interactive demo, not
a style URL.

**`liberty` is the default**, and the upstream README says why: *"All the OpenMapTiles
styles (Bright, Positron, Dark, Fiord) are abandoned by their upstream project. Liberty
is fresh and alive."* Dark and Fiord work — verified `200` with valid style JSON — but
the README notes them incomplete and *"not offered on the Quick Start guide for the
moment"*, so they are choices, not first-run.

### No proxy. What we lose is identity, not assets.

**Dropped, and this is the right call.** MapLibre is handed the style URL and does its
own fetching. No `/map/t/{*path}`, no origin rewriting, no SSRF guard, no cache headers.

The proxy was never about assets. Panning and zooming generate tile requests, but that
is exactly what the **browser's HTTP cache** is built for, and the browser is the right
thing to make them. A proxy would make panning *worse* — a Python hop per tile, hundreds
per pan — while buying one thing: OpenFreeMap does not see the IP.

| | Proxy | Direct |
|---|---|---|
| Browser IP disclosed | no | **yes, to OpenFreeMap** |
| Backend tile code | ~80 lines + SSRF surface | **zero** |
| Failing-open rewrite bug | possible | **impossible** |
| Panning | every tile through Python | browser-native |
| Caching | hand-written `Cache-Control` | the browser's own |
| Tile version stamps | proxy must track them | **upstream's problem** |

There *was* a real leak to fix: the style document carries absolute `sprite` and
`glyphs` URLs, and its `openmaptiles` source is a TileJSON `url` whose **response**
carries absolute tile URLs — two hops, and a style-only rewrite fixes only the first.
With no proxy that bug class does not exist. There is nothing to rewrite and nothing to
get wrong.

**This is a deliberate departure from the `/image-proxy` precedent**, so state it rather
than smuggle it: `image_proxy` exists because *"rendering search thumbnails directly
would make the browser contact third-party hosts, leaking the user's IP"*
(`main.py:887`). Map tiles are the same shape of problem. The difference is that
OpenFreeMap needs no key and a tile request carries a viewport, not what the user said
— the disclosure is bounded and documented rather than silent. **If the house prefers
the invariant, that is a legitimate veto: reinstate the proxy as Phase 3 work.** Say so
now, not after it ships.

`map_tiles_base_url` reaches the browser via `/settings`. It is our own `.env`, never
user input, so passing it to a client is not an SSRF vector — but the frontend must
treat it as the trusted constant it is and never build a tile URL from anything the user
said.

### 3D is `pitch`, and which styles allow it is configuration

Measured, because it should not be guessed:

| Style | `fill-extrusion` | `terrain` | `projection` |
|---|---|---|---|
| **liberty** | **1** (`building-3d` ← `render_height`) | no | none |
| bright, positron, dark, fiord | 0 | no | none |

Liberty's 3D buildings are already baked in — pitch the camera and they extrude. There is
no 3D *style* to switch to and **no DEM anywhere**, which is why the expensive dataset
stays avoided. `terrain`, `sky`, `fog` and globe projection stay off permanently.

`supports3d` is **config, not `=== "liberty"`** — today only Liberty qualifies, but a
field costs nothing and makes a future 3D style a config edit:

```python
# assistant/backend/maps/config.py
MAP_STYLES = [
    {"id": "liberty",  "label": "Liberty",  "supports3d": True,  "point_color": "#c2410c", "label_color": "#1c1917"},
    {"id": "bright",   "label": "Bright",   "supports3d": False, "point_color": "#2563eb", "label_color": "#1c1917"},
    {"id": "positron", "label": "Positron", "supports3d": False, "point_color": "#2563eb", "label_color": "#1c1917"},
    {"id": "dark",      "label": "Dark",     "supports3d": False, "point_color": "#fb923c", "label_color": "#e7e5e4"},
    {"id": "fiord",     "label": "Fiord",    "supports3d": False, "point_color": "#38bdf8", "label_color": "#e0f2fe"},
]
```

Shipped in `/settings`. **The client never sniffs a style's layers** — which is why the
colours come from the same list instead of being derived in two places and disagreeing.

The **pitch control** is a camera control, not a style:

- Toggle plus a `pitch` range (0–60). Enables `dragRotate` / `pitchWithRotate`;
  `maxPitch: 60` so the horizon never becomes a black void.
- Enabled when the active style has `supports3d`; disabled with a stated reason
  otherwise (*"this basemap has no 3D buildings"*). Not hidden — a control that vanishes
  is a control nobody finds.
- Untoggling resets `pitch` to `0`. A map left tilted reads as a bug.
- A `@pytest.mark.network` test asserts declared `supports3d` matches the live
  `fill-extrusion` count. Config can be wrong; the test catches it when upstream changes
  and the map silently renders flat.

Style switches **never re-frame** — `setStyle` preserves the camera, which is what you
want when comparing the same points across two basemaps.

### Privacy

There is no separate tiles switch and no no-tiles mode. **Enabling maps is the consent
act**: `MAPS_ENABLED` ships **false**, and it gates the TopBar button, the routes, and
tile rendering together. Turning maps on is the user saying "fetch a basemap from
OpenFreeMap." **A dot on a map is a location; a basemap under it turns that into a
map.** Set `MAP_TILES_BASE_URL` to a self-hosted OpenFreeMap to remove even that, at the
cost of running the tile stack yourself.

`docs/MAPS.md` states the disclosure in plain words rather than leaving it in a diff.

MapLibre renders attribution from the style — **do not suppress it**; ODbL attribution
is a licence condition.

## API — two new endpoints

| Route | Purpose |
|---|---|
| `POST /maps` | `{name}` → canonical slug, write an empty `FeatureCollection`, resolve the frame, return `{frame_id, name}`. The New Map button needs somewhere to POST. |
| `POST /maps/{frame_id}/points` | `{lat, lon, name?, description?}` → append (or merge) one point and return the updated frame. This is the click path; the coordinate comes from the user's pointer, so it needs no provenance gate. It calls the same `merge_points` the tools do. |
| `GET /settings` | **extended** — `maps_enabled`, `map_tiles_base_url`, `map_styles`. Same pattern as the existing `brave_enabled`. No separate tiles flag: maps enabled *is* tiles enabled, and the button/routes read `maps_enabled`. |

Everything else is reused: `GET /files/list`, `GET /files/{frame_id}`,
`DELETE /files/{frame_id}`.

Two consequences of reading the raw file instead of a parsed response:

- **No `point_count` on the switcher.** Getting it means parsing every file per list
  request, or caching a count — the one duplication this plan exists to prevent. The
  switcher shows the map name and nothing else. No requirement asks for a count; the
  *open* map can show one for free because its data is in hand.
- **`/files/{id}` returns `content: str`** decoded with `errors="replace"`, so a
  hand-edited non-UTF8 file fails `JSON.parse` and surfaces as *"this file isn't a valid
  map"* — an honest message.

`GET /files/{frame_id}` returning the file's own bytes means **no second serialiser** and
nothing can drift between disk and canvas.

`map_ref: {frame_id, name}` rides the existing **`meta` SSE event**
(`streaming.py:71-96`), mirroring `search_info`. Not a new event type, and not
`tool_result` — the frontend ignores `tool_result` data today (`state/chat.ts:206`),
so that would be a fragile side channel. **Plumbing:** the frame id is born inside a
tool handler, so `stream_tool_loop` captures the latest map tool's `ToolResult.data`
into a `map_ref` and the orchestrator passes it to `MetaEvent`. That is new code, not
"the existing event for free."

## Frontend

### MapLibre GL JS v6 directly

BSD-3-Clause. Chosen over **Leaflet** (DOM/CSS markers; MapLibre draws every point in
one GeoJSON source and one `circle` layer) and Mapbox GL (not OSS).

**No maintained Solid wrapper.** `solid-maplibre` is **0.5.0, published 2025-03-09**,
MIT, `peerDependencies: { "maplibre-gl": "*", "solid-js": "*" }`, no release in the **19
months** since. A peer range of `"*"` is not a compatibility claim, it is an absence of
one — and two MapLibre majors have shipped underneath it. `maplibre-gl` is **6.12.0**
and **removed the default export** (`import { Map, setWorkerUrl } from 'maplibre-gl'`);
`setWorkerUrl` is mandatory under Vite (`maplibre-gl-worker.mjs?worker&url`). So:
**~150 lines of thin Solid layer.**

**Why the Brain graph appears in this document at all:** it is the only component here
that mounts a large WebGL library into Solid, so it has already paid for the mistakes
such a component invites. Six rules, none about graphs:

| Rule | Precedent |
|---|---|
| Props are **accessors**, never destructured | `BrainGraph3D.tsx:9-22` |
| Every prop read in a library callback is `untrack()`ed | `:223-224`, `:292`, `:304-306` |
| Mutable local `let map` + one readiness signal | `:143-154` |
| Init gated on real layout dimensions (rAF on `offsetWidth`) | `:324-336` |
| Teardown in **both** `onCleanup` and the init `catch` | `:311-320`, `:338-345` |
| Capability gate with graceful fallback | `:261-264` — **but see below** |

**Nothing else about the Brain page carries over** — no mode toggle (3D here is a
camera), no touring, no hover tooltips, no conflict highlighting, no 2D fallback. That is
a brain-shaped feature answering brain-shaped questions.

That gate checks `typeof WebGLRenderingContext === 'undefined'` — WebGL **1**. MapLibre
v6 needs **WebGL2**, so copy the shape, not the constant.

`maplibre-gl/dist/maplibre-gl.css` must be imported from **inside** the lazily-imported
module so Vite code-splits it; a global import ships ~100 KB of map CSS to the chat page.
Verify in the bundle diff, not by assumption.

### `MapCanvas.tsx`

Lazy-loaded. Style and `is3d` are props; `supports3d` gates whether the pitch control is
offered at all.

- **Exactly one Map instance, for the page's lifetime — a requirement, not an
  optimisation.** Only one map is ever open: no multi-map state, no preloading, nothing
  to keep in sync. Picking a different map fetches that one file and calls `setData()` +
  `fitBounds()` on the live instance. A WebGL context is scarce (~8–16/page) and
  permanently held, so tearing one down per switch is the wrong reflex.
- **`addPointLayers` on initial load AND on `style.load`, idempotent.** `setStyle()`
  replaces the whole style, so a source and layers added with `addSource`/`addLayer` are
  gone and every point vanishes on the first switch — the most likely reason "switch
  between views" ships broken. One function owns the point source and its layers, and
  takes the style's `point_color`/`label_color` from config.
- **One GeoJSON source.** `cluster: true`, `clusterMaxZoom` ≈ 10 — the band where a
  world view stops being readable. Runs in MapLibre's worker; expand via
  `getClusterExpansionZoom`.
- **Half the points have no name.** A `symbol` layer shows `name` when present and
  **nothing when absent** — no placeholder, no invented title. Coordinates and
  description go in the detail panel on click.
- Colour by a `category` property where present, else the style's `point_color`. **Not
  a per-point random one** — that would imply a distinction the data does not have.
- **`fitBounds` on load and when the point set changes shape.** Adding a point re-frames
  the map; that is the whole interaction.
- **Click to add.** A `＋ Add point` toggle puts the canvas in add mode; the next click
  converts screen → `lngLat`, POSTs `/maps/{frame_id}/points`, and updates the live
  source. A mode toggle, not a bare click, so panning and popup dismissal never drop a
  stray point. The new point is unnamed; the detail panel labels it. The screen →
  `[lon, lat]` conversion lives in one helper so the axis order is written once.
- **WebGL2 absent → a readable list of points, not a blank box.**
- Every library callback reads props through `untrack()`. `map.remove()` in `onCleanup`
  **and** in the init `catch`.
- `ResizeObserver` on the container — **new to the codebase**; existing views use
  `window.addEventListener('resize')` (`BrainPage.tsx:56`).
- Points are also a focusable list: a WebGL canvas is not keyboard navigable.

### `MapPage.tsx`

Layout from **`FilesPage.tsx`** (its line numbers moved; see the changes note above).
Switcher left, one canvas right — the shape req 3
asks for and the shape the codebase already has. Reuse the existing `.files-content` /
`.files-main` / `<aside>` classes from `files.css`; `map.css` holds map-only selectors and
must not redefine a class `files.css` already defines (`designSystem.test.ts` fails on a
class defined in two stylesheets).

- `routes.tsx` — `/maps` and `/maps/:frameId`. **Selection is a URL**, so back, refresh
  and sharing work. Both existing pages keep selection in a signal, so this is a
  deliberate departure: with one map open at a time, being able to return to it and send
  someone its link is the point of a switcher.
- Switcher reads `GET /files/list`, filters `name.endsWith('.geojson')`, **name only**.
  Clicking navigates and does **not** remount the canvas.
- Open map reads `GET /files/{frame_id}` and `JSON.parse`s `.content`. One request, one
  map, no prefetch.
- **New map** → `POST /maps`, then navigate to the returned frame id. **Delete** →
  `DELETE /files/{frame_id}`, then `/maps`.
- Zero points → an empty state and a hint to click the map (or talk to the assistant),
  never a bare grey rectangle.
- **Style picker** over `map_styles`. A native
  `<select>` — five options is not a toolbar, and a native control is
  keyboard-navigable and screen-reader-labelled for free. Choosing one calls `setStyle`
  and **must not re-frame**. `createEffect(on(() => props.styleId, applyStyle))` applies
  to the live instance instead of remounting.
- **Pitch control** beside it, enabled per `supports3d`.
- Detail panel: name, description, coordinates; edit label, remove, and a **download
  GeoJSON** action fetching `GET /files/{frame_id}` and offering it as a blob — no new
  endpoint for a file the browser already has. A point dropped by a click is unnamed
  until labelled here.

**Style choice and camera live in `localStorage`.** They are user preferences; putting
either in the database is the duplication this plan exists to prevent. Default `liberty`.
Camera is per map id; the style is app-wide, because a basemap preference does not
belong to one map.

### TopBar button

`TopBar.tsx:326-333` — a `topbar-btn` anchor beside Files and Brain, rendered only when
`/settings` reports `maps_enabled`:

```
<a href="/maps" class="topbar-btn" title="Maps"><MapIcon />Maps</a>
```

`MapIcon` goes in `TopBarIcons.tsx` as a folded-map glyph in the 24×24 `currentColor`
stroked style that file's header comment fixes — no emoji, no icon font, no dependency.

**Four existing assertions change, in two tests** — the part that gets half-done and
leaves a red suite:

| Location | Change |
|---|---|
| `TopBar.test.tsx` | the test **title** — now `'orders the right side Alerts, Let's talk, Files, Brain, Trace, Settings, Trash'` — must gain `Maps`. Miss this and the test *name* is a lie about what it pins. |
| the same test | `expect(labels).toHaveLength(7)` → `8`; insert `expect(labels[3]).toBe('Maps')`; shift Brain→4, Trace→5, Settings→6, Trash→7 |
| the SVG-icon test | its **second** `toHaveLength(7)` → `8` |

New order: `Alerts, Let's talk, Files, Maps, Brain, Trace, Settings, Trash`.
`ExternalLinkGuard` needs nothing — `isExternalHttpUrl('/maps')` is already `false` for
a same-origin path. `TopBar` already consumes `getSettings()` for `brave_enabled`
(`TopBar.tsx:118`), so `maps_enabled` follows the same path — and the test's settings
mock must report it `true`, or the button the assertions count is absent.

### No map in the transcript

A `map_ref` renders a **text chip** — map name, point count, link. **Not a MapLibre
preview.** Every `Map` instance holds a WebGL context permanently, so a transcript with
ten map messages exhausts them (~8–16/page) and fails as blank boxes mid-scrollback. One
canvas, on the page, ever — now a stated requirement, not just good sense.

### Styles

New `src/styles/map.css`, imported by `src/styles/index.css` beside `brain.css` and
`files.css`. The page reuses the existing layout classes; `map.css` adds only map-only
selectors. `src/styles/designSystem.test.ts` enforces no `<style>` blocks, no inline
styles except `--` vars, no undefined `var()`, and **no class defined in two
stylesheets** — so reusing `.files-content` means referencing it, not restating it.

## Phases

| Phase | Hours |
|---|---|
| 0 — Measure before building | 4 |
| 1 — File plumbing: geojson, slug, write path | 6 |
| 2 — Tools | 5 |
| 3 — `POST /maps`, point endpoint, `/settings`, `user_message` | 5 |
| 4 — `MapCanvas` + click-to-add + styles + pitch | 11 |
| 5 — Map page, switcher, TopBar | 7 |
| 6 — Ship | 4 |

Prerequisite, a separate change not counted here: drop the non-streaming tool loop (see
*Tools*). The maps work assumes one loop.

### Phase 0 — Measure before building (4h)

Per AGENTS.md, the riskiest belief gets an experiment before anything is built on it. Not
"can we render a map" — settled. With the click path carrying the common case, the open
question is narrower but real: **when the user types coordinates, does the model use the
tool correctly and keep them straight?**

`assistant/experiments/`, following `graph_walk_yield`'s pre-registration and
falsification conditions, read-only against a brain copy via `preflight.py`. Commit
`plan.md` before collecting data; `result.md` only after `verification.md`.

1. **Tool-call precision.** ≥95% schema-valid `save_map_points` on map-shaped requests.
   *Falsified* at >5% malformed args, or if selection on **non-map** requests degrades by
   >2% — the tool list is in every prompt, and that is the real cost.
2. **Target-map correctness.** ≥90% of points land on the map the user named, across
   phrasings. With several maps the failure is a near miss — correct points on a map
   called *"Assets 2"*. Invisible with one map, fatal to req 3.
3. **No invented coordinates.** Where the user's message has no coordinate pair, the
   model asks rather than calling the tool, ≥95% of the time. Both coordinates are
   required by the schema, so an invented value is a *valid* call that renders a wrong
   point — the failure the gate exists to catch.
4. **Coordinate order.** ≥95% reach the file as `[lon, lat]`. A swap is valid, renders,
   and is in the ocean. Below 95%, add an executor cross-check against the conversation
   text before writing.

A negative result here is a cheap save, not a failure.

### Phase 1 — File plumbing (6h)

- `geojson` into `SUPPORTED_UPLOAD_EXTS` and a dispatch entry; route the upload
  write through `write_sandbox_bytes` (`filesystem.py:165` — already exists, the
  atomic bytes writer with the traversal/symlink check) with `overwrite=True`,
  replacing the bare `open()` in `upload_file_to_memory`.
- `maps/config.py`, `maps/geojson.py` (write-side only), `maps/store.py`
  (`canonical_slug`, `resolve_map`, `merge_points`).

**The invariant test** — the regression guard for the architecture:

```python
resolve_map("Properties", 1)
save_points("Properties", twelve_points)
assert slot_keys(map_frame) <= {"file_name", "file_size", "file_ext", "file_safe_name"}
assert db.execute("SELECT COUNT(*) FROM slots WHERE value LIKE '%45.52%'").fetchone()[0] == 0
```

The moment someone helpfully caches a preview or a point count "for performance," it
fails. That is the point.

**Tests**: slug collisions produce two files, not one overwrite; traversal (`../`)
raises `PathTraversalError`; malformed GeoJSON raises rather than half-importing; `42`
and `42.000000` are one feature; a non-Point geometry is skipped and counted; a
half-coordinate is refused; an unwritable path leaves the original intact (atomicity); a
`.geojson` file dropped in by hand shows up in `/files/list` and is therefore a map;
**correcting a named point moves it without increasing the feature count**; a bare
coordinate added twice does not.

**The two-identity tests, which is where this feature actually breaks:** `"Properties"`
via the tool and an upload of `Properties.geojson` produce **one** file and **one**
frame; `resolve_map("PROPERTIES")` finds the same map; `canonical_slug` output
round-trips through `file_{Path(name).name}` so `delete_file` prunes the frame it should.

### Phase 2 — Tools (5h)

As specified, including the provenance gate and the correction semantics.

**Tests**: a batch of 12 → 12 features in one file; a coordinate the user typed is
accepted **with and without a space after the comma**; a coordinate the user did **not**
type is refused with **no file written**; a half-coordinate is refused; a selector of
`{}` is refused; a new name creates the file; **the agent cannot write outside the
sandbox because it never supplies a path.**

### Phase 3 — `POST /maps`, point endpoint, `/settings`, `user_message` (5h)

**Tests**: `POST /maps {"name": "Properties"}` then `GET /files/list` shows it and
`GET /files/{id}` returns an empty `FeatureCollection`; a name canonicalising onto an
existing map returns that map rather than a second file; `POST /maps/{id}/points`
appends a point and the file gains one feature; a concurrent click and tool write both
land (the lock); `/settings` carries the three keys; a `map_ref` on the `meta` event; and
the **`user_message` plumbing** — a coordinate in the user's own text reaches the gate
intact.

Plus the `@pytest.mark.network` `supports3d` check. Not in the default gate — the point
is that it is written and run whenever the style list is touched.

### Phase 4 — `MapCanvas` + click-to-add + styles + pitch (11h)

`MapCanvas.test.tsx`: import smoke test, a DOM mount assertion, and the WebGL2-absent
list fallback. jsdom cannot initialise MapLibre, so canvas behaviour is proven by hand —
**say that in the file** so nobody mistakes thin frontend tests for coverage. The
behaviours worth real tests are `addPointLayers` being idempotent across `setStyle` and
the screen → `lngLat` → `[lon, lat]` conversion, which lives in one pure helper.

`mapLib.test.ts` carries the maths that used to be Python: antimeridian bounds span ~102°,
not 258°; a one-point map returns `null` bounds; a bare-coordinate feature carries no
`null` properties; `42` and `42.000000` are one feature. **Pure functions, no jsdom, no
WebGL** — this is the file that covers the geometry, and it needs no browser scaffolding.

**Verify Liberty's pitch by hand once** at city zoom: drag to 60° and confirm buildings
extrude. The layer is in the style JSON, but a layer that renders nothing reads as a
broken feature rather than a missing one.

### Phase 5 — Map page, switcher, TopBar (7h)

`MapPage.test.tsx` with the `fetchMock` routing from `FilesPage.test.tsx`. Reuse the
`files.css` layout classes without redefining them. Update the four `TopBar.test.tsx`
assertions.

**Verify both Compose projects.** Dev (8443) and prod (8444) are separate projects with
separate images and databases; a source change reaches neither until rebuilt, so rebuild
**both** and hard-refresh (⌘⇧R). *"It still does the old thing"* is usually an un-rebuilt
environment or a stale bundle.

### Phase 6 — Ship (4h)

- `map_round_trip` — both paths: a click → `POST /maps/{id}/points` → file → rendered
  marker, and a typed coordinate → tool call → file. Coordinate correctness, `[lon, lat]`
  order end to end.
- `map_usage` — maps, points per map, how often the user **switches between them** and
  opens `/maps` at all. This is the measurement that would kill the feature: *"two maps
  in six months, never revisited"*, or *"twenty maps of one point each, because name
  resolution kept failing"* — a Phase 0 failure surfacing late.
- `provenance_rejects` — persistently non-zero means the prompt wording needs work; zero
  on turns that should have had coordinates means the gate is not looking back far
  enough.
- Docs: `docs/MAPS.md` (including the tile disclosure), an `AGENTS.md` note,
  `.env.example` entries.
- **Delete this plan file** when the work ships (RUNBOOK §4). Nothing else to
  remove — the superseded frames-based plan went with this rewrite.

## Test strategy

**The feature's thesis in one test:**

> **`test_map_correction.py`** — the user says *"put 45.523, -122.68 on the properties
> map"*; the file gains one feature with `coordinates == [-122.68, 45.523]`. The user
> says *"no, that's 45.524"*; the coordinate changes and **the feature count is still
> one**. Then assert the coordinate order explicitly — that assertion *is* the feature.

If this breaks, the map is inverted and nobody notices for months.

Also: `test_maps.py` (the Phase 1 matrix including the invariant assertion),
`test_map_tools.py`, a `POST /maps/{id}/points` test (append, half-coordinate refusal,
concurrent write), `mapLib.test.ts`, and frontend `MapCanvas.test.tsx` /
`MapPage.test.tsx` plus the updated `TopBar.test.tsx`.

**Gates before merge**: `ruff check .`, `pytest assistant/tests/`, `npm run lint`,
`npm run typecheck`, `npm run test`, plus the mandated `test_daily_schedule.py` +
`test_review_fixes.py`.

## Rollback

| Change | Reversal |
|---|---|
| `geojson` in `SUPPORTED_UPLOAD_EXTS` | Remove the string; the file still reads as plain text via the dispatch fallback. |
| Two tools | `register_tool` is a dict write; remove from `builtin_tools` and `TOOL_REGISTRY`. |
| `user_message` plumbing | A `ContextVar` set at the one call site, plus a store read for prior turns. Additive. |
| `canonical_slug` + case-insensitive resolve | Pure functions plus one branch. Reverting leaves possibly-duplicated files; `/files/list` still lists them. |
| `POST /maps`, `POST /maps/{id}/points` | Delete the routes. The agent can still create and update maps by name. |
| `/settings` additions | Three keys in an existing dict. |
| Frontend | Delete `MapCanvas`, `MapPage`, `MapIcon`, the `routes.tsx` lines, the `TopBar.tsx` anchor, `src/styles/map.css` and its `index.css` import; revert the four `TopBar.test.tsx` assertions. |
| Map files | Ordinary files, still listed at `/files/list` and readable at `/files/{id}`. `assistant db backup` first. |
| Map frames | **Use `prune_file_frame`** (`store.py:747`) — it already cascades `part_of` children and is what `delete_file` uses. Do **not** hand-roll `SELECT id FROM frames WHERE name LIKE 'file_%.geojson'` → `prune_frames`: it works but duplicates existing logic, and this project's rule is to verify callers before writing a second copy. |
| `maplibre-gl` | One `package.json` line. |
| `MAPS_ENABLED` / `MAP_TILES_BASE_URL` | Keys in `config.py`. Off leaves the page unreachable, as it was before. |

**Do not roll back frames with `prune_frames_by_source_type`.** It selects
`WHERE source_type = ?` (`store.py:733`) and `user` / `user_correction` are the source
types of **every frame the user has ever stated in conversation** (`confidence.py:13`,
`extractor.py:1376`). `prune_frames_by_source_type("user")` is not a rollback, it is
`rm -rf` on memory. Also: no `deleted_at` filter — a soft-deleted frame still holds
slots — and `prune_frames` returns *the ids you passed* whether or not they existed
(`store.py:719`), so count the SELECT first.

## Non-goals

- **Trip planning** — ordering, routes, dates. A map is a bag of points.
- **Live position feeds** — a moving coordinate is a different model.
- **Geometry beyond points.** Lines and polygons are *skipped*, not converted.
- **A spatial index** / "what is near me" / haversine in SQL. Bounds come from the
  features in hand.
- **POI search, directions, routing** — different projects.
- **Geocoding.** Out of scope. An address is not a coordinate, and the only ways to get
  one are a third-party call or a self-hosted geocoder (Nominatim/Photon/Addok), both
  real infrastructure for a household feature. The user adds points by clicking the map,
  and the agent path requires coordinates in the user's own words. If geocoding is ever
  wanted, the shape is a `Geocoder` ABC (a local service on `appnet`, like SearXNG),
  gated on a measurement of how often a point arrives as an address with no coordinate.
- **Semantic recall over points.** If ever needed, an embedding index *rebuilt from the
  file* — derived, so it cannot drift.
- **Maps shared between people.** A GeoJSON file is shareable by construction. That
  dissolves the multi-tenant problem rather than solving it.
- **Terrain / DEM.** Measured: no style carries a `terrain` member, and Liberty's 3D is
  a `fill-extrusion` over `render_height`.
- **A tile proxy.** Deliberate, argued above, and reversible as one phase.
