import { render, screen, waitFor } from '@solidjs/testing-library'
import { describe, expect, it } from 'vitest'

import type { MapStyle } from '../../types'
import MapCanvas from './MapCanvas'

const STYLE: MapStyle = {
  id: 'liberty',
  label: 'Liberty',
  supports3d: true,
  point_color: '#c2410c',
  label_color: '#1c1917',
}

/**
 * jsdom cannot initialise MapLibre, so this file proves only the shell: the
 * container renders, and WebGL2-absent degrades to a readable list rather than a
 * blank box. Canvas behaviour is proven by hand in a browser — do not mistake
 * these for coverage of the map itself.
 */
describe('MapCanvas', () => {
  it('falls back to a readable list when WebGL2 is unavailable (jsdom)', async () => {
    render(() => (
      <MapCanvas
        data={{
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [-122.68, 45.523] },
              properties: { description: 'shed' },
            },
          ],
        }}
        style={STYLE}
        tilesBaseUrl="https://tiles.openfreemap.org/styles"
        addMode={false}
        pitch={0}
        onAddPoint={() => {}}
        onSelect={() => {}}
      />
    ))

    await waitFor(() => expect(screen.getByRole('note')).toBeInTheDocument())
    expect(screen.getByText(/shed/)).toBeInTheDocument()
  })
})
