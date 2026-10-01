import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, waitFor } from '@solidjs/testing-library'
import { createRoot } from 'solid-js'
import { useActiveConversation } from './useActiveConversation'
import { setUser } from '../state/user'
import { setSessionId } from '../state/chat'
import type { Session } from '../types'
import * as api from '../services/api'

vi.mock('../services/api')

/**
 * The alerts panel resolves an alert into a conversation and announces it with a
 * CustomEvent, so the panel does not need to import chat state. These tests pin the
 * other half: that the announcement actually moves the chat there.
 *
 * Without this, clicking a conversation in the picker would silently do nothing —
 * the alert would be attached server-side while the user stared at an unchanged
 * screen.
 */
describe('useActiveConversation — opening a conversation from an alert', () => {
  const sessions: Session[] = [
    { id: 'conv_a', title: 'First', episode_count: 3, last_activity: '2026-09-30T10:00:00Z', created_at: '2026-09-30T10:00:00Z' },
    { id: 'conv_b', title: 'Second', episode_count: 5, last_activity: '2026-10-01T10:00:00Z', created_at: '2026-10-01T10:00:00Z' },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    setUser({ id: 1, name: 'Test User' })
    setSessionId(null)
    vi.mocked(api.getSessionMessages).mockResolvedValue([])
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  /** Mount the hook; returns a live accessor for the active conversation. */
  function mount(available: Session[] = sessions) {
    let hook: ReturnType<typeof useActiveConversation> | undefined
    let dispose = () => {}
    createRoot((d) => {
      dispose = d
      hook = useActiveConversation(() => available)
    })
    return {
      get: () => hook?.activeConversation(),
      dispose,
    }
  }

  it('switches to a conversation named in the event', async () => {
    const harness = mount()
    render(() => null)

    window.dispatchEvent(
      new CustomEvent('open-conversation', { detail: { sessionId: 'conv_a' } })
    )

    await waitFor(() => expect(harness.get()?.id).toBe('conv_a'))
    harness.dispose()
  })

  it('opens a conversation that is not in the fetched list yet', async () => {
    // The panel loads options from the alert's own endpoint, so the target may not
    // be in the sidebar's list. It must still open.
    const harness = mount(sessions)
    render(() => null)

    window.dispatchEvent(
      new CustomEvent('open-conversation', {
        detail: { sessionId: 'conv_alert_4560' },
      })
    )

    await waitFor(() => expect(harness.get()?.id).toBe('conv_alert_4560'))
    harness.dispose()
  })

  it('ignores an event with no session id', async () => {
    const harness = mount()
    render(() => null)

    window.dispatchEvent(new CustomEvent('open-conversation', { detail: {} }))

    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(harness.get()).toBeNull()
    harness.dispose()
  })
})
