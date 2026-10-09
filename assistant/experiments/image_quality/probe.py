"""Ad-hoc probe: what images does Brave actually return, and how good are they?

For a set of queries, record each result's `thumbnail.src` (preview) and
`thumbnail.original` (full), then fetch both and measure status, content-type,
bytes, and pixel dimensions. No brain, no writes.

    python assistant/experiments/image_quality/probe.py
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
from pathlib import Path

import httpx

from assistant.backend.config import settings
from assistant.backend.pipeline.search import BraveBackend

HERE = Path(__file__).resolve().parent

QUERIES = [
    "Sabrina Gonzalez Pasterski Perimeter Institute",
    "celestial holography spin memory effect",
    "best acoustic guitar strings for beginners",
    "Austin Texas music venues indie rock",
    "how do lithium batteries degrade",
]

_UA = "Mozilla/5.0 (compatible; AssistantBot/1.0)"


def _dims(data: bytes) -> tuple[int, int] | None:
    """(width, height) from PNG/JPEG/GIF headers, or None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:3] == b"GIF":
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return w, h
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
    except Exception:
        return None
    return None


async def _fetch(client: httpx.AsyncClient, url: str) -> dict:
    try:
        r = await client.get(url, headers={"User-Agent": _UA})
        dims = _dims(r.content) if r.status_code == 200 else None
        return {
            "status": r.status_code,
            "type": (r.headers.get("content-type") or "").split(";")[0],
            "bytes": len(r.content),
            "dims": dims,
        }
    except Exception as e:
        return {"status": f"ERR:{type(e).__name__}", "type": "", "bytes": 0, "dims": None}


async def main() -> None:
    backend = BraveBackend(api_key=settings.brave_api_key)
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for query in QUERIES:
            results, _videos = await backend.search(query, num_results=6)
            print(f"\n=== {query} ({len(results)} results) ===")
            for r in results:
                preview = r.thumbnail
                full = r.image
                distinct = bool(full and full != preview)
                p = await _fetch(client, preview) if preview else {}
                f = await _fetch(client, full) if distinct else {}
                rows.append(
                    {
                        "query": query,
                        "title": r.title[:70],
                        "url": r.url,
                        "has_distinct_full": distinct,
                        "preview": p,
                        "full": f,
                    }
                )
                print(f"  {r.title[:50]!r}")
                print(
                    f"    preview {p.get('status')} {p.get('dims')} {p.get('bytes')}B"
                )
                if distinct:
                    print(
                        f"    full    {f.get('status')} {f.get('dims')} {f.get('bytes')}B"
                    )
                else:
                    print("    full    (none distinct -> preview is the only image)")
    await backend.close()

    total = len(rows)
    distinct = sum(1 for r in rows if r["has_distinct_full"])
    preview_dims = [r["preview"].get("dims") for r in rows if r["preview"].get("dims")]
    full_ok = [
        r["full"].get("dims")
        for r in rows
        if r["has_distinct_full"] and r["full"].get("status") == 200
    ]
    full_blocked = sum(
        1
        for r in rows
        if r["has_distinct_full"] and r["full"].get("status") != 200
    )
    print("\n=== SUMMARY ===")
    print(f"results: {total}")
    print(f"with a distinct full image: {distinct} ({100*distinct//max(total,1)}%)")
    print(f"full image blocked/failed: {full_blocked} of {distinct}")
    if preview_dims:
        print(f"preview dims: {preview_dims}")
    if full_ok:
        print(f"full dims (ok): {full_ok}")
    (HERE / "result.json").write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {HERE / 'result.json'}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
