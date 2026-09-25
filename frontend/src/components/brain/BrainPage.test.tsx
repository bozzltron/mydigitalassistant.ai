import { render, waitFor, fireEvent, screen } from '@solidjs/testing-library'
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

  it('searches memory topics and opens the frame detail from a match', async () => {
    const matches = [
      {
        frame: FRAMES[1],
        slots: [],
        similarity: 0.9,
        associations: [],
        episodes: [],
        conflicts: [],
      },
    ]
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/memory/frames') return Promise.resolve(jsonResponse(FRAMES))
      if (url === '/memory/associations') return Promise.resolve(jsonResponse(ASSOCIATIONS))
      if (url === '/memory/conflicts') return Promise.resolve(jsonResponse(CONFLICTS))
      if (url === '/assistant/name') return Promise.resolve(jsonResponse({ name: 'Echo' }))
      if (url.startsWith('/memory/search')) {
        return Promise.resolve(jsonResponse({
          query: 'quantum',
          semantic_search: false,
          backend_rev: 0,
          matches,
          summary: '',
        }))
      }
      return Promise.resolve(jsonResponse([]))
    })

    render(() => <BrainPage />)
    await waitFor(() => {
      expect(document.querySelectorAll('.brain-canvas .node circle').length).toBeGreaterThan(0)
    })

    fireEvent.input(screen.getByPlaceholderText('Search memory by topic...'), {
      target: { value: 'quantum' },
    })
    fireEvent.submit(screen.getByPlaceholderText('Search memory by topic...').closest('form')!)

    await waitFor(() => {
      expect(document.querySelector('.topic-card')).not.toBeNull()
    })
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/memory/search?q=quantum'),
    )

    fireEvent.click(screen.getByText('Quantum computing')!)

    await waitFor(() => {
      expect(document.querySelector('.frame-detail')).not.toBeNull()
    })
    expect(document.querySelector('.frame-detail h3')?.textContent).toBe('Quantum computing')
  })

  it('resolves a pending conflict from the 2D tooltip and reloads memory', async () => {
    render(() => <BrainPage />)
    await waitFor(() => {
      expect(document.querySelectorAll('.brain-canvas .node circle').length).toBe(FRAMES.length)
    })

    const firstNode = document.querySelector('.brain-canvas .node') as HTMLElement
    fireEvent.mouseOver(firstNode.querySelector('circle')!)

    await waitFor(() => {
      expect(document.querySelector('.tooltip')?.classList.contains('visible')).toBe(true)
      expect(document.querySelector('[data-conflict-resolve]')).not.toBeNull()
    })

    fireEvent.click(document.querySelector('[data-conflict-resolve].use-new') as HTMLElement)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/memory/conflicts/12/resolve?value=Alice'),
        expect.objectContaining({ method: 'POST' }),
      )
    })

    // Resolution reloads brain data (fresh /memory/frames fetch).
    await waitFor(() => {
      const frameCalls = fetchMock.mock.calls.filter(([u]) => String(u) === '/memory/frames')
      expect(frameCalls.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('shows the empty state when memory has no frames', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/memory/frames') return Promise.resolve(jsonResponse([]))
      if (url === '/memory/associations') return Promise.resolve(jsonResponse([]))
      if (url === '/memory/conflicts') return Promise.resolve(jsonResponse([]))
      if (url === '/assistant/name') return Promise.resolve(jsonResponse({ name: 'Echo' }))
      return Promise.resolve(jsonResponse([]))
    })

    render(() => <BrainPage />)
    await waitFor(() => {
      expect(document.querySelector('.empty-state')).not.toBeNull()
    })
    expect(document.querySelector('.empty-state h2')?.textContent).toBe('No memories yet')
  })

  it('shows an error banner when memory loading fails', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/memory/frames') return Promise.reject(new Error('boom'))
      return Promise.resolve(jsonResponse([]))
    })

    render(() => <BrainPage />)
    await waitFor(() => {
      expect(document.querySelector('.error-banner')?.textContent).toContain('Failed to load brain data')
    })
  })
})
