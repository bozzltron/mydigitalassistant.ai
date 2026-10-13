"""GeoJSON for the map page: validate a FeatureCollection and canonicalise it.

Write-side only and pure — no I/O. The map page owns the file; the server checks
the shape and normalises it so the file stays clean and a coordinate transposition
(a *valid* GeoJSON point in the ocean) is caught at the boundary rather than
rendered confidently forever.

A map is points. Only `Point` geometry is accepted; anything else is refused, not
silently converted.
"""

from __future__ import annotations

import json

# A household map. A runaway client is refused, not written.
MAX_FEATURES = 5000
MAX_DESCRIPTION = 2000


class GeoJSONError(ValueError):
    """A FeatureCollection the map page sent is not valid GeoJSON we will store."""


def empty_fc() -> dict:
    """An empty FeatureCollection — the map with no points."""
    return {"type": "FeatureCollection", "features": []}


def _coord(value, *, low: float, high: float, axis: str) -> float:
    # bool is an int subclass; a JSON `true` is not a coordinate.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeoJSONError(f"{axis} must be a number")
    if value < low or value > high:
        raise GeoJSONError(f"{axis} {value} is out of range [{low}, {high}]")
    return round(float(value), 6)


def validate_fc(data) -> dict:
    """Return a canonical FeatureCollection, or raise `GeoJSONError`.

    Points only, both coordinates required, `[lon, lat]` order (GeoJSON is
    longitude-first — a swap is valid and renders in the ocean), and never both
    zero (the Gulf of Guinea: always a swap or a placeholder). Coordinates are
    rounded to 6 dp (~0.11 m), so `42` and `42.000000` are one point. A point with
    no description keeps an empty `properties` object; a null is never written.
    """
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise GeoJSONError("not a FeatureCollection")
    features = data.get("features")
    if not isinstance(features, list):
        raise GeoJSONError("features must be a list")
    if len(features) > MAX_FEATURES:
        raise GeoJSONError(f"too many features ({len(features)} > {MAX_FEATURES})")

    out: list[dict] = []
    for i, feat in enumerate(features):
        if not isinstance(feat, dict) or feat.get("type") != "Feature":
            raise GeoJSONError(f"feature {i} is not a Feature")
        geom = feat.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            raise GeoJSONError(f"feature {i} is not a Point")
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) != 2:
            raise GeoJSONError(f"feature {i} needs exactly [lon, lat]")
        lon = _coord(coords[0], low=-180, high=180, axis="longitude")
        lat = _coord(coords[1], low=-90, high=90, axis="latitude")
        if lon == 0 and lat == 0:
            raise GeoJSONError(f"feature {i} is at (0, 0) — a swap or a placeholder")

        props_in = feat.get("properties")
        if props_in is not None and not isinstance(props_in, dict):
            raise GeoJSONError(f"feature {i} properties must be an object")
        description = (props_in or {}).get("description")
        props: dict = {}
        if isinstance(description, str) and description.strip():
            props["description"] = description.strip()[:MAX_DESCRIPTION]

        out.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )

    return {"type": "FeatureCollection", "features": out}


def dumps(fc: dict) -> str:
    """The file's text: indent 2, trailing newline, unicode kept readable.

    The user may open this in a text editor, and the agent reads it as text.
    """
    return json.dumps(fc, indent=2, ensure_ascii=False) + "\n"
