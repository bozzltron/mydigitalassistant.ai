import { render, waitFor } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import BrainPage from './BrainPage'

/**
 * Regression test for the Brain Observatory graph.
 *
 * The 2D view previously crashed before rendering any node (undefined
 * `setNeighborsMap` / out-of-scope highlight helpers in `render2D`), so the
 * brain never drew. This test pins the happy path: with saved 2D mode and a
 * populated memory API, the SVG graph actually renders one circle per frame
 * and marks conflict frames.
 */

const FRAMES = [
  {
    id: 1,
    name: 'Alice',
    type: 'person',
    confidence: 0.8,
    essential: 1,
    priority: 0.6,
    owner_user_id: null,
    source_type: null,
    source_url: null,
    source_reliability: null,
    embedding_model: null,
    created_at: null,
    updated_at: null,
    deleted_at: null,
  },
  {
    id: 2,
    name: 'Quantum computing',
    type: 'concept',
    confidence: 0.5,
    essential: 0,
    priority: 0.4,
    owner_user_id: null,
    source_type: null,
    source_url: null,
    source_reliability: null,
    embedding_model: null,
    created_at: null,
    updated_at: null,
    deleted_at: null,
  },
]

const ASSOCIATIONS = [
  {
    id: 1,
    from_frame_id: 1,
    to_frame_id: 2,
    relation_type: 'studies',
    confidence: 0.7,
    essential: 0,
    priority: 0.5,
    source_type: null,
    source_url: null,
    source_reliability: null,
    embedding_model: null,
    created_at: null,
  },
]

const CONFLICTS = [
  {
    id: 12,
    frame_id: 1,
    slot_key: 'nickname',
    existing_value: 'Al',
    new_value: 'Alice',
    resolved_value: null,
    status: 'pending',
    created_at: null,
    resolved_at: null,
  },
]

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  localStorage.setItem('brain-view', '2d')
  fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/memory/frames') return Promise.resolve(jsonResponse(FRAMES))
    if (url === '/memory/associations') return Promise.resolve(jsonResponse(ASSOCIATIONS))
    if (url === '/memory/conflicts') return Promise.resolve(jsonResponse(CONFLICTS))
    if (url === '/assistant/name') return Promise.resolve(jsonResponse({ name: 'Echo' }))
    return Promise.resolve(jsonResponse([]))
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.removeItem('brain-view')
})

describe('BrainPage', () => {
  it('renders the 2D brain graph with one circle per frame', async () => {
    render(() => <BrainPage />)

    await waitFor(() => {
      const circles = document.querySelectorAll('.brain-canvas .nodes .node circle')
      expect(circles.length).toBe(FRAMES.length)
    })

    expect(document.querySelector('.brain-canvas .node.has-conflict')).not.toBeNull()
    expect(fetchMock).toHaveBeenCalledWith('/memory/frames')
    expect(fetchMock).toHaveBeenCalledWith('/memory/associations')
    expect(fetchMock).toHaveBeenCalledWith('/memory/conflicts')
  })

  it('respects the saved 2D view mode without loading the WebGL 3D view', async () => {
    render(() => <BrainPage />)

    await waitFor(() => {
      expect(document.querySelectorAll('.brain-canvas .node circle').length).toBeGreaterThan(0)
    })

    expect(document.getElementById('brain-canvas-3d')).toBeNull()
    expect(document.querySelector('.brain-canvas')).not.toBeNull()
  })

  it('shows agent name and node/edge stats once loaded', async () => {
    render(() => <BrainPage />)

    await waitFor(() => {
      expect(document.getElementById('agent-name')?.textContent).toBe('Echo')
    })

    const statsText = document.querySelector('.stats-bar')?.textContent ?? ''
    expect(statsText).toContain('2 nodes')
    expect(statsText).toContain('1 edges')
  })

  it('mounts the 3D viewer canvas when the saved view is 3d', async () => {
    localStorage.setItem('brain-view', '3d')
    render(() => <BrainPage />)

    // The 3D container mounts (init is a no-op in jsdom: no WebGL, no layout).
    await waitFor(() => {
      expect(document.getElementById('brain-canvas-3d')).not.toBeNull()
    })

    expect(fetchMock).toHaveBeenCalledWith('/memory/frames')
    expect(fetchMock).toHaveBeenCalledWith('/memory/conflicts')
  })
})
