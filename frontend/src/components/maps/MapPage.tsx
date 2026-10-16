import { createMemo, createSignal, For, onMount, Show } from 'solid-js'
import { getMap, getSettings, listMapFiles, putMap } from '../../services/api'
import { debug } from '../../services/logger'
import type { MapFeatureCollection, MapStyle } from '../../types'
import { describeFeature, emptyCollection, pointFeature } from '../../utils/mapLib'
import PageMenu from '../ui/PageMenu'
import MapCanvas from './MapCanvas'

const STYLE_KEY = 'map_style'
/** Which map file the page is working on. A map is an ordinary sandbox file. */
const FILE_KEY = 'map_file'
const DEFAULT_FILE = 'map.geojson'

/** Mirrors the backend's default when `/settings` does not carry a style list. */
const FALLBACK_STYLE: MapStyle = {
  id: 'liberty',
  label: 'Liberty',
  supports3d: true,
  point_color: '#c2410c',
  label_color: '#1c1917',
  label_font: ['Noto Sans Regular'],
}

/**
 * `/maps` — the map page. One canvas, and one *chosen* file from the sandbox.
 *
 * A map is an ordinary sandbox file, so there can be more than one — the picker lists
 * the `.geojson` files at the sandbox root. The page is the only writer: it loads the
 * chosen file, edits it in memory, and PUTs the whole FeatureCollection on every change.
 * The agent reads the same files with the file tools. See `docs/MAPS.md`.
 */
export default function MapPage() {
  const [enabled, setEnabled] = createSignal<boolean | null>(null)
  const [tilesBase, setTilesBase] = createSignal('')
  const [styles, setStyles] = createSignal<MapStyle[]>([])
  const [styleId, setStyleId] = createSignal(
    (typeof localStorage !== 'undefined' ? localStorage.getItem(STYLE_KEY) : null) || 'liberty'
  )
  const [files, setFiles] = createSignal<string[]>([DEFAULT_FILE])
  const [file, setFile] = createSignal(
    (typeof localStorage !== 'undefined' ? localStorage.getItem(FILE_KEY) : null) || DEFAULT_FILE
  )
  const [data, setData] = createSignal<MapFeatureCollection>(emptyCollection())
  const [selected, setSelected] = createSignal<number | null>(null)
  const [draft, setDraft] = createSignal<{ lon: number; lat: number } | null>(null)
  const [pitch, setPitch] = createSignal(0)
  const [saving, setSaving] = createSignal(false)
  const [error, setError] = createSignal('')

  onMount(async () => {
    try {
      const s = await getSettings()
      setEnabled(s.maps_enabled)
      setTilesBase(s.map_tiles_base_url)
      setStyles(s.map_styles)
      const firstStyle = s.map_styles[0]
      if (firstStyle && !s.map_styles.some((x) => x.id === styleId())) {
        setStyleId(firstStyle.id)
      }
      debug('[maps] settings', { maps_enabled: s.maps_enabled, styles: s.map_styles.length })
      if (!s.maps_enabled) return
      const listing = await listMapFiles()
      setFiles(listing.files)
      // A remembered file may have been deleted outside the page; fall back rather than
      // opening an empty canvas with a name nothing backs.
      if (!listing.files.includes(file())) setFile(DEFAULT_FILE)
      const fc = await getMap(file())
      debug('[maps] map loaded', { file: file(), features: fc?.features?.length ?? 'null' })
      setData(fc)
    } catch (e) {
      console.error('[maps] failed to load the map page', e)
      setError('Could not reach the assistant.')
      setEnabled(false)
    }
  })

  // The style list comes from `/settings`; the fallback keeps the canvas renderable
  // if a backend (or a test mock) omits it.
  const activeStyle = createMemo(
    () => styles().find((s) => s.id === styleId()) ?? styles()[0] ?? FALLBACK_STYLE
  )

  // The page owns the file, so every change is a whole-file PUT. Optimistic: the
  // change is shown first, and a failed save says so and leaves it on screen.
  const save = async (next: MapFeatureCollection) => {
    setSaving(true)
    setError('')
    debug('[maps] saving', next.features.length, 'feature(s)')
    try {
      const saved = await putMap(next, file())
      debug('[maps] saved', saved?.features?.length ?? 'null')
      setData(saved)
    } catch (e) {
      console.error('[maps] save failed', e)
      setError('Could not save the map. Your change is still here — try again.')
    } finally {
      setSaving(false)
    }
  }

  const commit = (features: MapFeatureCollection['features']) => {
    const next = { ...data(), features }
    setData(next)
    void save(next)
  }

  const confirmDraft = (description: string) => {
    const d = draft()
    if (!d) return
    const next = [...data().features, pointFeature(d.lon, d.lat, description)]
    setDraft(null)
    setSelected(next.length - 1)
    commit(next)
  }

  const editSelected = (description: string) => {
    const i = selected()
    if (i === null) return
    commit(
      data().features.map((f, k) =>
        k === i
          ? { ...f, properties: description.trim() ? { description: description.trim() } : {} }
          : f
      )
    )
  }

  /**
   * Open another map file.
   *
   * Nothing is written by switching: the page PUTs only on a change. The canvas
   * re-frames itself when the point set changes, so no camera work is needed here.
   */
  const switchFile = async (name: string) => {
    if (name === file()) return
    setFile(name)
    if (typeof localStorage !== 'undefined') localStorage.setItem(FILE_KEY, name)
    setSelected(null)
    setDraft(null)
    setError('')
    try {
      const fc = await getMap(name)
      debug('[maps] switched', name, fc?.features?.length ?? 'null')
      setData(fc)
    } catch (e) {
      console.error('[maps] could not open the map', name, e)
      setError(`Could not open ${name}.`)
    }
  }

  const removeSelected = () => {
    const i = selected()
    if (i === null) return
    setSelected(null)
    commit(data().features.filter((_, k) => k !== i))
  }

  return (
    <div class="map-page">
      <Show
        when={enabled()}
        fallback={
          <div class="map-disabled">
            {/* The menu belongs on the page in both states. With maps off this page had
                no way to reach any other one — and maps are off by default in prod, so
                that was the *normal* case, not the edge. */}
            <PageMenu />
            <Show when={enabled() === null}>
              <p>Loading…</p>
            </Show>
            <Show when={enabled() === false}>
              <h1>Maps are off</h1>
              <p>
                Enabling maps fetches a basemap from OpenFreeMap, so your browser contacts
                their servers directly. Set <code>MAPS_ENABLED=true</code> to turn it on.
              </p>
            </Show>
          </div>
        }
      >
        <header class="map-toolbar">
          <h1>Map</h1>
          <PageMenu />
          <label class="map-field">
            Map
            <select
              value={file()}
              onChange={(e) => void switchFile(e.currentTarget.value)}
            >
              <For each={files()}>{(f) => <option value={f}>{f}</option>}</For>
            </select>
          </label>
          <label class="map-field">
            Basemap
            <select
              value={styleId()}
              onChange={(e) => {
                setStyleId(e.currentTarget.value)
                if (typeof localStorage !== 'undefined') {
                  localStorage.setItem(STYLE_KEY, e.currentTarget.value)
                }
              }}
            >
              <For each={styles()}>{(s) => <option value={s.id}>{s.label}</option>}</For>
            </select>
          </label>
          <Show when={activeStyle()?.supports3d}>
            <label class="map-field">
              Pitch
              <input
                type="range"
                min="0"
                max="60"
                value={pitch()}
                onInput={(e) => setPitch(Number(e.currentTarget.value))}
              />
            </label>
          </Show>
          <span class="map-status" aria-live="polite">
            {saving() ? 'Saving…' : error()}
          </span>
        </header>

        <div class="map-body">
          <div class="map-stage">
            <MapCanvas
              data={data()}
              draft={draft()}
              style={activeStyle()}
              tilesBaseUrl={tilesBase()}
              pitch={pitch()}
              onAddPoint={(lon, lat) => {
                setDraft({ lon, lat })
              }}
              onSelect={(i) => {
                // The panel shows one form at a time; asking for a saved point
                // replaces an unsaved draft rather than leaving two in play.
                setDraft(null)
                setSelected(i)
              }}
            />
            <Show when={data().features.length === 0 && !draft()}>
              <p class="map-empty">Click anywhere on the map to add your first point.</p>
            </Show>
          </div>

          <aside class="map-panel">
            <Show when={draft()}>
              {(d) => (
                <PointForm
                  title="New point"
                  initial=""
                  saving={saving()}
                  onCancel={() => setDraft(null)}
                  onConfirm={confirmDraft}
                  hint={`${d().lat.toFixed(5)}, ${d().lon.toFixed(5)}`}
                />
              )}
            </Show>

            <Show when={!draft() && selected() !== null && data().features[selected()!]}>
              {(f) => (
                <PointForm
                  title="Point"
                  initial={describeFeature(f())}
                  saving={saving()}
                  onCancel={() => setSelected(null)}
                  onConfirm={editSelected}
                  onRemove={removeSelected}
                  hint={`${f().geometry.coordinates[1].toFixed(5)}, ${f().geometry.coordinates[0].toFixed(5)}`}
                />
              )}
            </Show>

            <Show when={!draft() && selected() === null}>
              <p class="map-panel-hint">
                {data().features.length === 0
                  ? 'Click the map to add your first point.'
                  : `${data().features.length} saved. Click the map to add another, or click a point to edit it.`}
              </p>
            </Show>

            <p class="map-panel-note">
              Saved as <code>map.geojson</code> in your sandbox, so you can ask the assistant
              about it in conversation.
            </p>
          </aside>
        </div>
      </Show>
    </div>
  )
}

function PointForm(props: {
  title: string
  initial: string
  saving: boolean
  hint: string
  onConfirm: (description: string) => void
  onCancel: () => void
  onRemove?: () => void
}) {
  const [text, setText] = createSignal(props.initial)
  return (
    <form
      class="map-form"
      onSubmit={(e) => {
        e.preventDefault()
        props.onConfirm(text())
      }}
    >
      <h2>{props.title}</h2>
      <p class="map-form-coords">{props.hint}</p>
      <textarea
        rows="3"
        placeholder="What is here?"
        value={text()}
        onInput={(e) => setText(e.currentTarget.value)}
      />
      <div class="map-form-actions">
        <button type="submit" class="topbar-btn" disabled={props.saving}>
          Save
        </button>
        <button type="button" class="topbar-btn" onClick={() => props.onCancel()}>
          Cancel
        </button>
        <Show when={props.onRemove}>
          <button type="button" class="topbar-btn map-remove" onClick={() => props.onRemove?.()}>
            Remove
          </button>
        </Show>
      </div>
    </form>
  )
}
