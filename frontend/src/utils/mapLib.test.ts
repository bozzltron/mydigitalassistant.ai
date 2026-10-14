import { describe, expect, it } from 'vitest'
import {
  boundsFor,
  clickAction,
  describeFeature,
  emptyCollection,
  pointFeature,
  round6,
} from './mapLib'

describe('pointFeature', () => {
  it('writes [lon, lat] — the axis order the whole feature rests on', () => {
    const f = pointFeature(-122.68, 45.523, 'shed')
    expect(f.geometry.coordinates).toEqual([-122.68, 45.523])
    expect(f.properties).toEqual({ description: 'shed' })
  })

  it('rounds to 6 dp, so 42 and 42.000000 are one point', () => {
    expect(pointFeature(42.0, 42.0000001).geometry.coordinates).toEqual([42, 42])
    expect(round6(1.23456789)).toBe(1.234568)
  })

  it('omits the description when there is none — never a null', () => {
    expect(pointFeature(1, 1).properties).toEqual({})
    expect(pointFeature(1, 1, '   ').properties).toEqual({})
  })

  it('trims and caps the description', () => {
    expect(describeFeature(pointFeature(1, 1, '  hi  '))).toBe('hi')
    expect(describeFeature(pointFeature(1, 1, 'x'.repeat(5000))).length).toBe(2000)
  })
})

describe('boundsFor', () => {
  it('is null for fewer than two points (a lone point keeps its camera)', () => {
    expect(boundsFor([])).toBeNull()
    expect(boundsFor([pointFeature(1, 1)])).toBeNull()
  })

  it('frames a normal spread', () => {
    const b = boundsFor([pointFeature(-10, 0), pointFeature(10, 5)])
    expect(b).toEqual([
      [-10, 0],
      [10, 5],
    ])
  })

  it('takes the short arc across the antimeridian: ~102°, not 258°', () => {
    const b = boundsFor([pointFeature(-122.7, 40), pointFeature(135.7, 45)])!
    const span = b[1][0] - b[0][0]
    expect(span).toBeCloseTo(101.6, 1)
    expect(span).toBeLessThan(180)
  })
})

describe('emptyCollection', () => {
  it('is a FeatureCollection with no features', () => {
    expect(emptyCollection()).toEqual({ type: 'FeatureCollection', features: [] })
  })
})

describe('clickAction — the whole interaction, since there is no add mode', () => {
  it('starts a new point on bare basemap', () => {
    expect(clickAction([], 12.5, 41.9)).toEqual({ kind: 'add', lon: 12.5, lat: 41.9 })
  })

  it('selects a saved point by its index', () => {
    // `generateId` makes the feature id the index into `data.features`, which is
    // what lets a click address a point at all.
    expect(clickAction([{ id: 3, properties: { description: 'shed' } }], 0, 0)).toEqual({
      kind: 'select',
      index: 3,
    })
  })

  it('selects index 0 — the falsy id that `index || 0` still writes', () => {
    expect(clickAction([{ id: 0, properties: {} }], 0, 0)).toEqual({ kind: 'select', index: 0 })
  })

  it('prefers a cluster over a point beneath it, whatever the hit order', () => {
    const cluster = { id: 1, properties: { cluster: true, cluster_id: 17, point_count: 4 } }
    const point = { id: 2, properties: { description: 'shed' } }
    expect(clickAction([cluster], 0, 0)).toEqual({ kind: 'cluster', clusterId: 17 })
    expect(clickAction([point, cluster], 0, 0)).toEqual({ kind: 'cluster', clusterId: 17 })
    expect(clickAction([cluster, point], 0, 0)).toEqual({ kind: 'cluster', clusterId: 17 })
  })

  it('reads a label hit as the point it labels', () => {
    expect(
      clickAction([{ id: 2, properties: { description: 'shed' } }, { id: 9 }], 0, 0)
    ).toEqual({ kind: 'select', index: 2 })
  })

  it('adds when the hit carries no usable id — never selects the wrong point', () => {
    expect(clickAction([{ properties: { description: 'shed' } }], 5, 6)).toEqual({
      kind: 'add',
      lon: 5,
      lat: 6,
    })
    expect(clickAction([{ id: 'a-string-id' }], 5, 6)).toEqual({ kind: 'add', lon: 5, lat: 6 })
  })
})
