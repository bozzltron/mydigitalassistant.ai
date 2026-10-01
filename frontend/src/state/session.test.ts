import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../services/api', () => ({
  getUserSessions: vi.fn(),
  createNewConversation: vi.fn(),
  updateConversationTitle: vi.fn(),
  getUsers: vi.fn(),
}))

import { fetchSessions, sessionTitle, sortByRecentActivity } from './session'
import * as api from '../services/api'

describe('sessionTitle', () => {
  it('uses the backend label carried in last_message', () => {
    expect(sessionTitle({ last_message: 'What is the capital of Texas?', episode_count: 3 }))
      .toBe('What is the capital of Texas?')
  })

  it('falls back to a generic name when the label is blank', () => {
    expect(sessionTitle({ last_message: '   ', episode_count: 4 })).toBe('Conversation 4')
  })
})

describe('fetchSessions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('maps the sessions payload to titled conversations', async () => {
    // Regression: the endpoint returns { id, episode_count, last_activity,
    // last_message } with NO `title` field, and `last_message` carries the
    // display label (title, else first user message). Reading `title` directly
    // left every conversation rendering as "Conversation".
    vi.mocked(api.getUserSessions).mockResolvedValue([
      { id: 'conv_1', episode_count: 2, last_activity: '2026-01-01', created_at: '2025-12-01', last_message: 'Hello there' },
    ])

    const sessions = await fetchSessions(1)

    expect(sessions).toEqual([
      { id: 'conv_1', title: 'Hello there', episode_count: 2, last_activity: '2026-01-01', created_at: '2025-12-01' },
    ])
  })
})

describe('sortByRecentActivity', () => {
  const s = (id: string, last_activity: string | null, created_at: string | null = null) => ({
    id,
    title: id,
    episode_count: 1,
    last_activity,
    created_at,
  })

  it('orders most recently active first', () => {
    const sorted = sortByRecentActivity([
      s('old', '2026-01-01T00:00:00'),
      s('new', '2026-03-01T00:00:00'),
      s('mid', '2026-02-01T00:00:00'),
    ])
    expect(sorted.map((x) => x.id)).toEqual(['new', 'mid', 'old'])
  })

  it('orders a never-used conversation by its creation time, so a new one leads', () => {
    // Regression: sorting every empty conversation last buried a just-created
    // one at the bottom of the switcher (a new "Corrections" conversation sat at
    // position 31 of 32, below the fold).
    const sorted = sortByRecentActivity([
      s('active', '2026-03-01T00:00:00', '2026-01-01T00:00:00'),
      s('fresh', null, '2026-04-01T00:00:00'),
    ])
    expect(sorted.map((x) => x.id)).toEqual(['fresh', 'active'])
  })

  it('still puts a conversation with neither timestamp last', () => {
    const sorted = sortByRecentActivity([
      s('unknown', null, null),
      s('active', '2026-03-01T00:00:00', '2026-01-01T00:00:00'),
    ])
    expect(sorted.map((x) => x.id)).toEqual(['active', 'unknown'])
  })
})
