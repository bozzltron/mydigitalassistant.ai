import { createEffect, createSignal, For, onCleanup, onMount, Show, untrack } from 'solid-js'
import type { MapFeatureCollection, MapStyle } from '../../types'
import { boundsFor } from '../../utils/mapLib'

const SOURCE = 'map-points'
const CIRCLE = 'map-points-circle'
const LABEL = 'map-points-label'

/**
 * The map canvas. `MapPage` lazy-loads this so MapLibre and its CSS are code-split
 * out of the chat bundle.
 *
 * **Exactly one `Map` instance for the page's lifetime.** A WebGL context is
 * scarce (~8–16/page) and permanently held, so a style switch calls `setStyle` on
 * the live map rather than tearing it down. `addPointLayers` runs on load *and* on
 * `style.load`, because `setStyle` replaces the whole style and takes the source
 * and its layers with it — the reason "switch styles" ships broken otherwise.
 */
export default function MapCanvas(props: {
  data: MapFeatureCollection
  style: MapStyle
  tilesBaseUrl: string
  addMode: boolean
  pitch: number
  onAddPoint: (lon: number, lat: number) => void
  onSelect: (index: number) => void
}) {
  let containerEl: HTMLDivElement | null = null
  function setContainerRef(el: HTMLDivElement | null) {
    containerEl = el
  }
  let map: import('maplibre-gl').Map | null = null
  let ml: typeof import('maplibre-gl') | null = null
  const [ready, setReady] = createSignal(false)
  const [failed, setFailed] = createSignal(false)

  const addPointLayers = () => {
    if (!map || !ml) return
    const style = untrack(() => props.style)
    if (!map.getSource(SOURCE)) {
      map.addSource(SOURCE, {
        type: 'geojson',
        data: untrack(() => props.data) as never,
        cluster: true,
        clusterMaxZoom: 10,
      })
    }
    if (!map.getLayer(CIRCLE)) {
      map.addLayer({
        id: CIRCLE,
        type: 'circle',
        source: SOURCE,
        paint: {
          'circle-color': style.point_color,
          'circle-radius': 6,
          'circle-stroke-width': 2,
          'circle-stroke-color': '#ffffff',
        },
      })
    }
    if (!map.getLayer(LABEL)) {
      map.addLayer({
        id: LABEL,
        type: 'symbol',
        source: SOURCE,
        // A point with no description shows a dot and no label — never a
        // placeholder or an invented title.
        filter: ['has', 'description'],
        layout: {
          'text-field': ['get', 'description'],
          'text-size': 12,
          'text-offset': [0, 1.2],
          'text-anchor': 'top',
        },
        paint: {
          'text-color': style.label_color,
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.5,
        },
      })
    }
  }

  const frameToData = () => {
    if (!map) return
    const bounds = boundsFor(untrack(() => props.data).features)
    if (bounds) map.fitBounds(bounds, { padding: 60, duration: 0 })
  }

  onMount(() => {
    let cancelled = false
    void (async () => {
      // MapLibre v6 needs WebGL2; the Brain graph's gate checks WebGL1, so copy
      // the shape, not the constant.
      if (typeof WebGL2RenderingContext === 'undefined') {
        setFailed(true)
        return
      }
      try {
        ml = await import('maplibre-gl')
        await import('maplibre-gl/dist/maplibre-gl.css')
      } catch {
        setFailed(true)
        return
      }
      if (cancelled || !ml || !containerEl) {
        if (!containerEl) setFailed(true)
        return
      }
      map = new ml.Map({
        container: containerEl,
        style: `${untrack(() => props.tilesBaseUrl)}/${untrack(() => props.style.id)}`,
        center: [0, 20],
        zoom: 1,
        maxPitch: 60,
      })
      map.on('load', () => {
        addPointLayers()
        frameToData()
        setReady(true)
      })
      map.on('style.load', addPointLayers)
      map.on('click', (e) => {
        // Only in add mode: a bare click would drop a stray point while panning.
        if (untrack(() => props.addMode)) {
          untrack(() => props.onAddPoint)(e.lngLat.lng, e.lngLat.lat)
        }
      })
    })()
    onCleanup(() => {
      cancelled = true
      map?.remove()
      map = null
    })
  })

  // A style switch applies to the live instance and never re-frames.
  createEffect(() => {
    const styleId = props.style.id
    if (!map || !ml || !ready()) return
    void styleId
    map.setStyle(`${untrack(() => props.tilesBaseUrl)}/${styleId}`)
  })

  // Data changes update the source; adding a point re-frames the map.
  createEffect(() => {
    const data = props.data
    if (!map || !ready()) return
    const src = map.getSource(SOURCE) as import('maplibre-gl').GeoJSONSource | undefined
    src?.setData(untrack(() => data) as never)
    frameToData()
  })

  createEffect(() => {
    const p = props.pitch
    if (!map || !ready()) return
    map.setPitch(p)
  })

  return (
    <>
      <Show when={failed()}>
        <div class="map-fallback" role="note">
          <p>This browser can’t draw the map (WebGL2 is unavailable). Your points:</p>
          <ul>
            <For each={props.data.features}>
              {(f, i) => (
                <li>
                  <button class="map-fallback-item" onClick={() => props.onSelect(i())}>
                    {f.geometry.coordinates[1].toFixed(5)}, {f.geometry.coordinates[0].toFixed(5)}
                    {f.properties.description ? ` — ${f.properties.description}` : ''}
                  </button>
                </li>
              )}
            </For>
          </ul>
        </div>
      </Show>
      <div
        ref={setContainerRef}
        class="map-canvas"
        classList={{ 'is-add-mode': props.addMode, 'is-hidden': failed() }}
      />
    </>
  )
}
