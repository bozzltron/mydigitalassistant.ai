"""Map basemap styles.

OpenFreeMap, no key. The client never sniffs a style's layers, so the colours live
here too — one source for both the picker and the renderer, rather than derived in
two places that disagree.

`supports3d` is config, not `style_id == "liberty"`: today only Liberty carries 3D
buildings (`fill-extrusion` over `render_height`), but a field makes a future 3D
style a config edit. See `docs/MAPS.md`.
"""

from __future__ import annotations

MAP_STYLES: list[dict] = [
    {"id": "liberty", "label": "Liberty", "supports3d": True,
     "point_color": "#c2410c", "label_color": "#1c1917"},
    {"id": "bright", "label": "Bright", "supports3d": False,
     "point_color": "#2563eb", "label_color": "#1c1917"},
    {"id": "positron", "label": "Positron", "supports3d": False,
     "point_color": "#2563eb", "label_color": "#1c1917"},
    {"id": "dark", "label": "Dark", "supports3d": False,
     "point_color": "#fb923c", "label_color": "#e7e5e4"},
    {"id": "fiord", "label": "Fiord", "supports3d": False,
     "point_color": "#38bdf8", "label_color": "#e0f2fe"},
]
