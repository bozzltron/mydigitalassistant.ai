import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../services/api', () => ({
  getUsers: vi.fn(),
}))

import { fetchUser, user } from './user'
import * as api from '../services/api'

describe('fetchUser', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    user() // touch to keep the import used
  })

  it('selects the last-used account when it still exists', async () => {
    localStorage.setItem('user_id', '2')
    vi.mocked(api.getUsers).mockResolvedValue([
      { id: 1, name: 'First', created_at: null },
      { id: 2, name: 'Second', created_at: null },
    ])

    await fetchUser()

    expect(user()?.id).toBe(2)
  })

  it('falls back to the first account and persists the choice', async () => {
    // Regression: this used to be a mock returning a hardcoded {id: 1}, so the
    // app scoped memory to an account it had never confirmed existed.
    vi.mocked(api.getUsers).mockResolvedValue([
      { id: 7, name: 'Solo', created_at: null },
    ])

    await fetchUser()

    expect(user()?.id).toBe(7)
    expect(localStorage.getItem('user_id')).toBe('7')
  })
})
