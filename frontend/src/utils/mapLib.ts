/**
 * Pure helpers for the map page: no DOM, no WebGL, so they are testable without a
 * browser. The one thing worth stating loudly — GeoJSON is `[lon, lat]` — and the
 * axis order is written here and nowhere else. A transposition is a *valid* point
 * in the ocean that renders confidently forever.
 */
import type { MapFeature, MapFeatureCollection } from '../types'

export const MAX_DESCRIPTION = 2000

/** Round to 6 dp (~0.11 m), matching the server's canonicalisation. */
export function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6
}

/** A point Feature, in `[lon, lat]` order. */
export function pointFeature(lon: number, lat: number, description?: string): MapFeature {
  const desc = (description ?? '').trim().slice(0, MAX_DESCRIPTION)
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [round6(lon), round6(lat)] },
    properties: desc ? { description: desc } : {},
  }
}

export function emptyCollection(): MapFeatureCollection {
  return { type: 'FeatureCollection', features: [] }
}

export function describeFeature(f: MapFeature): string {
  return f.properties?.description ?? ''
}

/**
 * Bounds for the feature set, antimeridian-aware, or `null` for fewer than two
 * points (one point gets no bounds and the page keeps its saved camera).
 *
 * Naive min/max frames the whole planet when points sit either side of the
 * antimeridian: 122.7°W and 135.7°E span 258° the long way, 102° the short way.
 * Split on the largest gap between consecutive longitudes and treat it as the
 * seam, so the bounds take the short arc.
 */
export function boundsFor(
  features: MapFeature[]
): [[number, number], [number, number]] | null {
  if (features.length < 2) return null
  const lats = features.map((f) => f.geometry.coordinates[1])
  const minLat = Math.min(...lats)
  const maxLat = Math.max(...lats)

  const sorted = features.map((f) => f.geometry.coordinates[0]).sort((a, b) => a - b)
  let gap = -Infinity
  let seam = 0
  for (let i = 0; i < sorted.length; i++) {
    const cur = sorted[i]!
    const next = i + 1 < sorted.length ? sorted[i + 1]! : sorted[0]! + 360
    if (next - cur > gap) {
      gap = next - cur
      seam = i
    }
  }
  const minLng = sorted[(seam + 1) % sorted.length]!
  let maxLng = sorted[seam]!
  if (maxLng < minLng) maxLng += 360
  return [
    [minLng, minLat],
    [maxLng, maxLat],
  ]
}
