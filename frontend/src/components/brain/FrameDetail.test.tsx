import { render, fireEvent, waitFor, screen } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import FrameDetail from './FrameDetail'
import type { BrainAssociation, BrainConflict, BrainFrame, BrainSlot } from '../../types'

const FRAME: BrainFrame = {
  id: 1,
  name: 'Alice',
  type: 'person',
  confidence: 0.8,
  essential: 1,
  priority: 0.6,
  owner_user_id: null,
  source_type: 'conversation',
  source_url: null,
  source_reliability: null,
  embedding_model: null,
  created_at: null,
  updated_at: null,
  deleted_at: null,
}

const SLOTS: BrainSlot[] = [
  {
    id: 7,
    frame_id: 1,
    key: 'nickname',
    value: 'Al',
    confidence: 0.9,
    essential: 1,
    priority: 0.5,
    source_type: null,
    source_url: null,
    source_reliability: null,
    source_episode_id: null,
    updated_at: null,
    last_strengthened_at: null,
  },
]

const ASSOCIATIONS: BrainAssociation[] = [
  {
    id: 3,
    from_frame_id: 1,
    to_frame_id: 2,
    relation_type: 'plays',
    confidence: 0.8,
    essential: 0,
    priority: 0.5,
    source_type: null,
    source_url: null,
    source_reliability: null,
    embedding_model: null,
    created_at: null,
  },
]

const CONFLICTS: BrainConflict[] = [
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
  fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith('/memory/frames/1/slots')) return Promise.resolve(jsonResponse(SLOTS))
    if (url.startsWith('/memory/frames/1/associations')) return Promise.resolve(jsonResponse(ASSOCIATIONS))
    return Promise.resolve(jsonResponse([]))
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FrameDetail', () => {
  it('renders slots, associations, and frame metadata', async () => {
    render(() => <FrameDetail frame={FRAME} conflicts={CONFLICTS} />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/memory/frames/1/slots')
      expect(fetchMock).toHaveBeenCalledWith('/memory/frames/1/associations')
      expect(document.querySelectorAll('.slots-table tbody tr').length).toBe(SLOTS.length)
    })

    expect(document.querySelector('.frame-detail h3')?.textContent).toBe('Alice')
    expect(screen.getByText('nickname')).not.toBeNull()
    expect(document.querySelector('.essential-badge')).not.toBeNull()
    expect(screen.getByText('plays')).not.toBeNull() // association relation label
  })

  it('shows pending conflicts and resolves via the chosen value', async () => {
    const onResolved = vi.fn()
    render(() => (
      <FrameDetail frame={FRAME} conflicts={CONFLICTS} onConflictResolved={onResolved} />
    ))

    await waitFor(() => {
      expect(screen.getByText('Keep existing')).not.toBeNull()
      expect(screen.getByText('Use new')).not.toBeNull()
    })

    fireEvent.click(screen.getByText('Use new'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/memory/conflicts/12/resolve?value=Alice'),
        expect.objectContaining({ method: 'POST' }),
      )
    })
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1))
  })

  it('fires onBack when the back button is clicked', () => {
    const onBack = vi.fn()
    render(() => <FrameDetail frame={FRAME} conflicts={[]} onBack={onBack} />)

    fireEvent.click(screen.getByText('← Back'))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('shows the fallback prompt when no frame is selected', () => {
    render(() => <FrameDetail conflicts={[]} />)
    expect(screen.getByText('No Frame Selected')).not.toBeNull()
    expect(screen.getByText('Select a frame from the graph to view details.')).not.toBeNull()
  })
})