import { render, screen, waitFor } from '@solidjs/testing-library'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getMap, getSettings } from '../../services/api'
import MapPage from './MapPage'

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  getMap: vi.fn(),
  putMap: vi.fn(),
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

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /add point/i })).toBeInTheDocument()
    )
    expect(getMap).toHaveBeenCalled()
  })
})
