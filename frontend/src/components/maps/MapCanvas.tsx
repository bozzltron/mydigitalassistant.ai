import { createEffect, createSignal, For, on, onCleanup, onMount, Show, untrack } from 'solid-js'
// MapLibre spawns a module worker; under Vite its URL must be given explicitly
// (`?worker&url` bundles it and hands back the URL) or the map never initialises —
// a blank canvas, and with no map there is no click handler to add a point.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'
import { debug } from '../../services/logger'
import type { MapFeatureCollection, MapStyle } from '../../types'
import { boundsFor } from '../../utils/mapLib'

const SOURCE = 'map-points'
const CIRCLE = 'map-points-circle'
const LABEL = 'map-points-label'
const TAG = '[maps]'

/**
 * The map canvas. `MapPage` lazy-loads this so MapLibre and its CSS are code-split
 * out of the chat bundle.
 *
 * **Exactly one `Map` instance for the page's lifetime.** A WebGL context is
 * scarce (~8–16/page) and permanently held, so a style switch calls `setStyle` on
 * the live map rather than tearing it down. `addPointLayers` runs on load *and* on
 * `style.load`, because `setStyle` replaces the whole style and takes the source
 * and its layers with it — the reason "switch styles" ships broken otherwise.
 *
 * Every step of the boot logs through `debug` (dev-only) and every genuine failure
 * through `console.error`, so a blank canvas says *why* it is blank.
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
  let map: MapLibreMap | null = null
  let ml: typeof import('maplibre-gl') | null = null
  const [ready, setReady] = createSignal(false)
  const [failed, setFailed] = createSignal(false)

  const styleUrl = (id: string) => `${untrack(() => props.tilesBaseUrl)}/${id}`

  const addPointLayers = () => {
    if (!map || !ml) return
    const style = untrack(() => props.style)
    if (!map.getSource(SOURCE)) {
      map.addSource(SOURCE, {
        type: 'geojson',
        data: untrack(() => props.data),
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
    debug(TAG, 'layers ready', {
      source: !!map.getSource(SOURCE),
      circle: !!map.getLayer(CIRCLE),
      label: !!map.getLayer(LABEL),
    })
  }

  const frameToData = () => {
    if (!map) return
    const bounds = boundsFor(untrack(() => props.data).features)
    if (bounds) map.fitBounds(bounds, { padding: 60, duration: 0 })
  }

  onMount(() => {
    let cancelled = false
    let observer: ResizeObserver | null = null
    let tries = 0

    const boot = async () => {
      debug(TAG, 'boot: start')
      // MapLibre v6 needs WebGL2; the Brain graph's gate checks WebGL1, so copy
      // the shape, not the constant.
      if (typeof WebGL2RenderingContext === 'undefined') {
        console.error(TAG, 'WebGL2 unavailable — cannot draw the map')
        setFailed(true)
        return
      }
      if (!ml) {
        debug(TAG, 'boot: importing maplibre-gl')
        try {
          ml = await import('maplibre-gl')
          await import('maplibre-gl/dist/maplibre-gl.css')
          ml.setWorkerUrl(maplibreWorkerUrl)
        } catch (e) {
          console.error(TAG, 'failed to load maplibre-gl', e)
          setFailed(true)
          return
        }
        debug(TAG, 'boot: maplibre loaded, worker set')
      }
      if (cancelled || !containerEl) {
        if (!containerEl) {
          console.error(TAG, 'boot: no container element')
          setFailed(true)
        }
        return
      }
      // Init gated on a real layout: a 0×0 container gives MapLibre nothing to
      // draw and the map stays blank. Retry a frame, bounded.
      if (containerEl.offsetWidth === 0 || containerEl.offsetHeight === 0) {
        if (++tries > 120) {
          console.error(TAG, 'boot: container never got a size')
          setFailed(true)
          return
        }
        requestAnimationFrame(() => void boot())
        return
      }
      debug(TAG, 'boot: container', `${containerEl.offsetWidth}×${containerEl.offsetHeight}`)

      const url = styleUrl(untrack(() => props.style.id))
      debug(TAG, 'creating Map', url)
      map = new ml.Map({ container: containerEl, style: url, center: [0, 20], zoom: 1, maxPitch: 60 })
      // The style fetch, the worker and tile loads all surface here — the one
      // place a blank canvas explains itself.
      map.on('error', (e) => console.error(TAG, 'maplibre error', e.error ?? e))
      map.on('load', () => {
        debug(TAG, 'map load')
        addPointLayers()
        frameToData()
        setReady(true)
      })
      map.on('style.load', () => {
        debug(TAG, 'style.load')
        addPointLayers()
      })
      map.on('click', (e) => {
        // Only in add mode: a bare click would drop a stray point while panning.
        const adding = untrack(() => props.addMode)
        debug(TAG, 'click', e.lngLat.lng, e.lngLat.lat, { addMode: adding })
        if (adding) untrack(() => props.onAddPoint)(e.lngLat.lng, e.lngLat.lat)
      })

      observer = new ResizeObserver(() => map?.resize())
      observer.observe(containerEl)
    }

    void boot()
    onCleanup(() => {
      cancelled = true
      observer?.disconnect()
      map?.remove()
      map = null
    })
  })

  // A style switch applies to the live instance and never re-frames. `on` so this
  // runs on a change, not on mount — the constructor already set the first style.
  createEffect(
    on(
      () => props.style.id,
      (id) => {
        if (!map || !ready()) return
        debug(TAG, 'setStyle', id)
        map.setStyle(styleUrl(id))
      },
      { defer: true }
    )
  )

  // Data changes update the source; adding a point re-frames the map.
  createEffect(
    on(
      () => props.data,
      (data) => {
        if (!map || !ready()) return
        const src = map.getSource(SOURCE) as GeoJSONSource | undefined
        debug(TAG, 'setData', data.features.length, 'feature(s)')
        src?.setData(data)
        frameToData()
      }
    )
  )

  createEffect(
    on(
      () => props.pitch,
      (p) => {
        if (map && ready()) map.setPitch(p)
      }
    )
  )

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
