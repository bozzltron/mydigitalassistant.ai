import { createEffect, createSignal, For, on, onCleanup, onMount, Show, untrack } from 'solid-js'
// MapLibre spawns a module worker; under Vite its URL must be given explicitly
// (`?worker&url` bundles it and hands back the URL) or the map never initialises —
// a blank canvas, and with no map there is no click handler to add a point.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'
import { debug } from '../../services/logger'
import type { MapFeatureCollection, MapStyle } from '../../types'
import { boundsFor, clickAction, pointFeature } from '../../utils/mapLib'

const SOURCE = 'map-points'
const CIRCLE = 'map-points-circle'
const LABEL = 'map-points-label'
// The pending point, drawn from its own source so it never touches the file until
// the description is confirmed.
const DRAFT = 'map-draft'
const DRAFT_CIRCLE = 'map-draft-circle'
const TAG = '[maps]'

/**
 * The map canvas.
 *
 * **MapLibre is imported inside `boot`, not at module scope**, so its bulk and its
 * CSS stay out of the chat bundle: the module is fetched only when the canvas has a
 * real size to draw into. (`MapPage` itself is imported statically by `routes.tsx` —
 * the page is a few kB; MapLibre is not.)
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
  draft: { lon: number; lat: number } | null
  style: MapStyle
  tilesBaseUrl: string
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

  const draftCollection = (): MapFeatureCollection => {
    const d = untrack(() => props.draft)
    return { type: 'FeatureCollection', features: d ? [pointFeature(d.lon, d.lat)] : [] }
  }

  const addPointLayers = () => {
    if (!map || !ml) return
    const style = untrack(() => props.style)
    if (!map.getSource(SOURCE)) {
      map.addSource(SOURCE, {
        type: 'geojson',
        data: untrack(() => props.data),
        cluster: true,
        clusterMaxZoom: 10,
        // MapLibre assigns `id` = the feature's index in the data (its worker:
        // `if (options.generateId) return index || 0`). That index is how a click
        // finds the feature it landed on — without it a click on a saved point is
        // indistinguishable from a click on bare basemap.
        generateId: true,
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
          // Naming the font is load-bearing: MapLibre's default (`Open Sans
          // Regular`) is not on OpenFreeMap's glyph endpoint, and glyphs that
          // failed to load have zero advance width — the text draws with every
          // character stacked on the last, which reads as unreadable overlap.
          'text-font': style.label_font,
          'text-size': 14,
          // Wrap a long description rather than drawing one line across the map.
          'text-max-width': 12,
          'text-offset': [0, 1.2],
          'text-anchor': 'top',
        },
        paint: {
          'text-color': style.label_color,
          'text-halo-color': '#ffffff',
          'text-halo-width': 2,
        },
      })
    }
    if (!map.getSource(DRAFT)) {
      map.addSource(DRAFT, { type: 'geojson', data: draftCollection() })
    }
    if (!map.getLayer(DRAFT_CIRCLE)) {
      map.addLayer({
        id: DRAFT_CIRCLE,
        type: 'circle',
        source: DRAFT,
        // Bigger and ringed, so a pending point reads as pending next to a saved one.
        paint: {
          'circle-color': style.point_color,
          'circle-radius': 9,
          'circle-stroke-width': 3,
          'circle-stroke-color': '#ffffff',
          'circle-opacity': 0.9,
        },
      })
    }
    debug(TAG, 'layers ready', {
      source: !!map.getSource(SOURCE),
      circle: !!map.getLayer(CIRCLE),
      label: !!map.getLayer(LABEL),
      draft: !!map.getLayer(DRAFT_CIRCLE),
    })
  }

  // The point *set*, not the whole FeatureCollection: an edit rewrites every
  // feature, so keying on the object itself would re-frame the camera on a
  // description change and yank it back from wherever the user had panned to.
  const geometryKey = (d: MapFeatureCollection) =>
    d.features.map((f) => f.geometry.coordinates.join(',')).join('|')
  let framedKey: string | null = null

  const frameToData = () => {
    if (!map) return
    const data = untrack(() => props.data)
    const bounds = boundsFor(data.features)
    if (bounds) map.fitBounds(bounds, { padding: 60, duration: 0 })
    framedKey = geometryKey(data)
  }

  const zoomToCluster = async (clusterId: number, lon: number, lat: number) => {
    const src = map?.getSource(SOURCE) as GeoJSONSource | undefined
    if (!src) return
    try {
      // `easeTo`, not `jumpTo`: the zoom is the feedback that the cluster was the
      // thing clicked, so it should read as movement, not a cut.
      const zoom = await src.getClusterExpansionZoom(clusterId)
      map?.easeTo({ center: [lon, lat], zoom })
    } catch (e) {
      // `setStyle` replaces the source, so an expansion asked of the old one can
      // outlive it. Nothing the user can act on.
      debug(TAG, 'cluster expansion abandoned', e)
    }
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
        // Log the canvas against its container. MapLibre silently falls back to
        // 400×300 when the container measures 0 on an axis (`clientWidth || 400`),
        // which yields a clipped, invisible map rather than an error — a mismatch
        // here is the signature of that, not of a bad style or tile.
        debug(TAG, 'map load', {
          canvas: `${map!.getCanvas().width}×${map!.getCanvas().height}`,
          container: `${containerEl!.clientWidth}×${containerEl!.clientHeight}`,
        })
        addPointLayers()
        frameToData()
        setReady(true)
      })
      map.on('style.load', () => {
        debug(TAG, 'style.load')
        addPointLayers()
      })
      map.on('click', (e) => {
        // MapLibre does not emit `click` for a drag, so panning cannot drop a point
        // — which is what the old `Add point` toggle existed to prevent.
        // The layers arrive with `load`; a click before then has nothing to
        // hit-test against, and MapLibre warns when a queried layer is absent.
        const hits = map!.getLayer(CIRCLE)
          ? map!.queryRenderedFeatures(e.point, { layers: [CIRCLE, LABEL] })
          : []
        const action = clickAction(
          hits.map((h) => ({ id: h.id, properties: h.properties })),
          e.lngLat.lng,
          e.lngLat.lat
        )
        debug(TAG, 'click', action)
        if (action.kind === 'cluster') {
          void zoomToCluster(action.clusterId, e.lngLat.lng, e.lngLat.lat)
        } else if (action.kind === 'select') {
          untrack(() => props.onSelect)(action.index)
        } else {
          untrack(() => props.onAddPoint)(action.lon, action.lat)
        }
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

  // Data changes update the source; a change to the point *set* re-frames the map.
  createEffect(
    on(
      () => props.data,
      (data) => {
        if (!map || !ready()) return
        const src = map.getSource(SOURCE) as GeoJSONSource | undefined
        debug(TAG, 'setData', data.features.length, 'feature(s)')
        src?.setData(data)
        if (geometryKey(data) !== framedKey) frameToData()
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

  // The pending point: drawn from its own source so it is visible the moment the
  // user clicks, without writing anything to the file until they confirm.
  createEffect(
    on(
      () => props.draft,
      () => {
        if (!map || !ready()) return
        const src = map.getSource(DRAFT) as GeoJSONSource | undefined
        src?.setData(draftCollection())
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
        classList={{ 'is-hidden': failed() }}
      />
    </>
  )
}
