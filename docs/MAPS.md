# Maps — a GeoJSON file in the sandbox, rendered on the Earth

A map is an ordinary sandbox file: **`map.geojson`** at the sandbox root. The map
page writes it; the agent reads it with the existing file tools. There is no map
store, no `map` frame type, and no `/maps` index — the file *is* the map.

## The file

A map is **any** GeoJSON file at the sandbox root, not one fixed name. The page's file
picker lists them (`GET /map/files` — root only, `.geojson` only) and every map request
names the one it is about (`GET`/`PUT /map?file=`). `map.geojson` is the default and is
always offered, even before it exists, because the page creates it on first save.

The name is validated at the boundary: a bare `.geojson` filename, no path separators, no
leading dot. The sandbox readers already guard traversal; a name that *could* escape is
refused where the refusal can say which rule broke.

Each file gets its own `file_<name>` frame, so every map is also a file the user can find
in the Files view.

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

- **Coordinates are `[lon, lat]`.** GeoJSON is longitude-first, and a transposition
  is a *valid* point in the ocean that renders confidently forever. The server
  validates at the boundary: `|lat| ≤ 90`, `|lon| ≤ 180`, **not both zero** (the
  Gulf of Guinea — always a swap or a placeholder), both present, rounded to 6 dp.
- **Points only.** A non-`Point` geometry is refused, not converted.
- **No `crs`** (RFC 7946 forbids it; WGS 84 assumed). A point with no description
  keeps an empty `properties` object — a `null` is never written.
- Indent 2, trailing newline, unicode kept readable: the user may open it in a text
  editor and the agent reads it as text.

## The page

`/maps` (behind `MAPS_ENABLED`). MapLibre GL JS on an OpenFreeMap basemap.

- **Click to add, click to edit — there is no mode.** A click on bare basemap opens a
  form for the description; the pending point is drawn from its own source, so it
  appears under the cursor immediately, but nothing reaches the file until Confirm.
  A click on a saved point selects it for editing; a click on a cluster zooms in.
  Panning cannot drop a stray point because MapLibre does not emit `click` for a
  drag, which is what the old `＋ Add point` toggle existed to guard.
- **Confirm** adds the point in memory and `PUT`s the whole FeatureCollection. The
  page addresses a clicked point by its index, which the source's `generateId`
  supplies — MapLibre assigns `id` = the feature's position in the data.
- **Edit and remove** from the point's detail panel.
- **The camera follows the point *set*.** Adding or removing a point re-frames to
  fit them all; editing a description does not. An edit rewrites the whole
  FeatureCollection, so re-framing on it would yank the camera back from wherever
  the user had panned to while they were only changing words.
- **Style picker** over `map_styles` (from `/settings`); choosing one calls
  `setStyle` and does not re-frame. **Pitch** is enabled when the active style has
  `supports3d` (Liberty only, today).
- **The label font is named, not inherited.** MapLibre's default `text-font` is
  `Open Sans Regular`, which OpenFreeMap answers **404** — and a glyph that failed
  to load has zero advance width, so the label still "rendered", with every
  character drawn on top of the last. That is unreadable overlap, not a
  missing-font error, which is why it is worth stating. `map_styles[].label_font`
  carries the font the tile server actually serves; the client never sniffs the
  style's layers for it (the rule `maps/config.py` already follows for colours).
- **One Map instance for the page's lifetime.** A WebGL context is scarce
  (~8–16/page) and permanently held, so a map is never mounted in the transcript —
  a `map_ref` would be a text chip.

## The agent

The file is at the sandbox root, so **no maps-specific code** is involved: the
agent reads it with `read_file("map.geojson")`, and `list_files` /
`glob("*.geojson")` find it. The agent never *writes* a map — there is no
coordinate-authoring tool.

Saving writes a `file_map.geojson` frame through `apply_file_to_memory` — the same
step the upload endpoint, `write_file` and `edit_file` funnel through — so the map
appears in the Files view like any other file the user owns. `GET /files/list` is a
view over file *frames*, so writing the bytes alone left the map on disk and
readable by the agent but **invisible to the user who made it**. The frame records
what the file *is* (name, size, extension, profile), never what it contains: the
point descriptions are read from the file, not from memory.

## Privacy: enabling maps is the consent

`MAPS_ENABLED` ships **false** and gates the TopBar button, the `/maps` route and
tile rendering **together**. Turning maps on is the user saying *"fetch a basemap
from OpenFreeMap."*

MapLibre is handed the style URL and fetches tiles from `tiles.openfreemap.org`
**directly from the browser**, so OpenFreeMap sees the user's IP and the viewport
they are looking at. **There is no tile proxy** — a Python hop per tile would make
panning worse and buys only the IP. Point `MAP_TILES_BASE_URL` at a self-hosted
OpenFreeMap to remove even that.

A dot on a map is a location; a basemap under it turns that into a map. That is why
the gate is the whole feature, not a separate tiles switch.

MapLibre renders attribution from the style — **do not suppress it**; OpenStreetMap
data is ODbL, and attribution is a licence condition.
