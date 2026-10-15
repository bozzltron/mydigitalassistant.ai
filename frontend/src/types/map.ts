/**
 * The map file (`map.geojson`) and the basemap styles from `/settings`.
 *
 * A map is a GeoJSON FeatureCollection of Points. Coordinates are `[lon, lat]`
 * — GeoJSON is longitude-first — and the axis order is written in exactly one
 * place (`utils/mapLib.ts`). See `docs/MAPS.md`.
 */

export interface MapFeature {
  type: 'Feature'
  geometry: { type: 'Point'; coordinates: [number, number] }
  /** Only `description` is stored; a point with none keeps `{}`. */
  properties: { description?: string }
}

export interface MapFeatureCollection {
  type: 'FeatureCollection'
  features: MapFeature[]
}

export interface MapStyle {
  id: string
  label: string
  supports3d: boolean
  point_color: string
  label_color: string
  /**
   * The font the basemap's glyph endpoint serves. Must be named: MapLibre's
   * default is `Open Sans Regular`, which OpenFreeMap 404s, and a missing glyph
   * has zero advance width — the label draws with every character stacked on the
   * last instead of failing visibly.
   */
  label_font: string[]
}
