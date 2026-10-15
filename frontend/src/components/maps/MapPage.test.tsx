import { fireEvent, render, screen, waitFor } from '@solidjs/testing-library'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getMap, getSettings } from '../../services/api'
import MapPage from './MapPage'

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  getMap: vi.fn(),
  putMap: vi.fn(),
}))

// jsdom cannot initialise MapLibre, so the canvas is stubbed down to the one thing
// this file is about: the click it reports upward.
vi.mock('./MapCanvas', () => ({
  default: (props: { onAddPoint: (lon: number, lat: number) => void }) => (
    <button onClick={() => props.onAddPoint(12.5, 41.9)}>map surface</button>
  ),
}))

const ENABLED = {
  brave_enabled: false,
  brave_configured: false,
  maps_enabled: true,
  map_tiles_base_url: 'https://tiles.openfreemap.org/styles',
  map_styles: [
    {
      id: 'liberty',
      label: 'Liberty',
      supports3d: true,
      point_color: '#c2410c',
      label_color: '#1c1917',
      label_font: ['Noto Sans Regular'],
    },
  ],
}

describe('MapPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('says maps are off when the backend has them disabled', async () => {
    vi.mocked(getSettings).mockResolvedValue({ ...ENABLED, maps_enabled: false })
    render(() => <MapPage />)
    await waitFor(() => expect(screen.getByText(/Maps are off/i)).toBeInTheDocument())
  })

  it('renders the toolbar and loads the map when enabled', async () => {
    vi.mocked(getSettings).mockResolvedValue(ENABLED)
    vi.mocked(getMap).mockResolvedValue({ type: 'FeatureCollection', features: [] })
    render(() => <MapPage />)

    await waitFor(() => expect(getMap).toHaveBeenCalled())
    expect(screen.getByRole('heading', { name: 'Map' })).toBeInTheDocument()
    // Adding a point is a click on the map, so the toolbar carries no mode toggle.
    expect(screen.queryByRole('button', { name: /add point/i })).not.toBeInTheDocument()
  })

  it('opens the description form straight from a map click, with nothing armed first', async () => {
    vi.mocked(getSettings).mockResolvedValue(ENABLED)
    vi.mocked(getMap).mockResolvedValue({ type: 'FeatureCollection', features: [] })
    render(() => <MapPage />)

    const surface = await screen.findByRole('button', { name: 'map surface' })
    fireEvent.click(surface)

    expect(await screen.findByRole('heading', { name: 'New point' })).toBeInTheDocument()
    // The form echoes the clicked coordinates, lat first — the axis order the user
    // reads, not the one the file stores.
    expect(screen.getByText('41.90000, 12.50000')).toBeInTheDocument()
  })
})
