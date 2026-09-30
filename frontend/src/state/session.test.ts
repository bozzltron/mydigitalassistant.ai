import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../services/api', () => ({
  getUserSessions: vi.fn(),
  createNewConversation: vi.fn(),
  updateConversationTitle: vi.fn(),
  getUsers: vi.fn(),
}))

import { fetchSessions, sessionTitle } from './session'
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
      { id: 'conv_1', episode_count: 2, last_activity: '2026-01-01', last_message: 'Hello there' },
    ])

    const sessions = await fetchSessions(1)

    expect(sessions).toEqual([
      { id: 'conv_1', title: 'Hello there', episode_count: 2, last_activity: '2026-01-01' },
    ])
  })
})
