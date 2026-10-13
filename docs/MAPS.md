# Maps — a GeoJSON file in the sandbox, rendered on the Earth

A map is an ordinary sandbox file: **`map.geojson`** at the sandbox root. The map
page writes it; the agent reads it with the existing file tools. There is no map
store, no `map` frame type, and no `/maps` index — the file *is* the map.

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

- **Click to add.** The `＋ Add point` toggle puts the canvas in add mode; the next
  click opens a form for the description. Confirm → the point is added in memory →
  the whole FeatureCollection is `PUT /map`. A mode toggle, not a bare click, so
  panning never drops a stray point.
- **Edit and remove** from the point's detail panel.
- **Style picker** over `map_styles` (from `/settings`); choosing one calls
  `setStyle` and does not re-frame. **Pitch** is enabled when the active style has
  `supports3d` (Liberty only, today).
- **One Map instance for the page's lifetime.** A WebGL context is scarce
  (~8–16/page) and permanently held, so a map is never mounted in the transcript —
  a `map_ref` would be a text chip.

## The agent

The file is at the sandbox root, so **no maps-specific code** is involved: the
agent reads it with `read_file("map.geojson")`, and `list_files` /
`glob("*.geojson")` find it. It carries **no memory frame** until something writes
it through `apply_file_to_memory`, so the agent reads it by path. The agent never
*writes* a map — there is no coordinate-authoring tool.

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
