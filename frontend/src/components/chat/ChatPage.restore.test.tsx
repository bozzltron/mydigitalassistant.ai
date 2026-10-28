import { render } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../../services/api', () => ({
  postChat: vi.fn(),
  postChatStream: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-restore'),
  getSessionMessages: vi.fn(async () => []),
  postFeedback: vi.fn(),
  getUsers: vi.fn(async () => []),
  postFileUpload: vi.fn(),
  listFiles: vi.fn(async () => []),
}))

vi.mock('../../services/status', () => ({
  useTurnStatus: () => ({ turnStatus: () => null, isPolling: () => false }),
  beginTurnStatus: vi.fn(),
  endTurnStatus: vi.fn(),
}))

vi.mock('../../hooks/useVoiceRecording', () => ({
  useVoiceRecording: () => ({ startDictation: vi.fn(), endDictation: vi.fn() }),
}))

// ChatPage.test.tsx covers the arrival rules against a store it seeds directly.
// The ordering rule — persisted queue is loaded *before* ChatPage seeds its
// arrival set — is only reachable when initQueue() actually runs, and it runs
// once per module instance. So this file gets a fresh module graph, with a
// saved queue in localStorage, and renders ChatPage against it.
describe('ChatPage restored queue on a fresh module load', () => {
  beforeEach(async () => {
    vi.resetModules()
    localStorage.clear()
    localStorage.setItem(
      'messageQueue',
      JSON.stringify([
        { id: 'restored-1', content: 'saved before reload', source: 'text', timestamp: 1 },
        { id: 'restored-2', content: 'also saved', source: 'text', timestamp: 2 },
      ])
    )
    const chat = await import('../../state/chat')
    chat.setSessionId('session-restore-1')
  })

  it('restores saved queue rows without marking them as new arrivals', async () => {
    const queue = await import('../../state/messageQueue')
    // A processing flag keeps the drain effect from emptying the panel before
    // the assertions run.
    queue.setProcessing(true)
    const { default: ChatPage } = await import('./ChatPage')

    render(() => <ChatPage conversation={null} />)

    // The saved rows must really have been loaded into the store by ChatPage
    // setup; otherwise the "unmarked" assertion below is vacuous.
    expect(queue.getQueue().map((q) => q.id)).toEqual(['restored-1', 'restored-2'])

    const rows = [...document.querySelectorAll<HTMLElement>('.msg-queued')]
    expect(rows.map((row) => row.id)).toEqual(['restored-1', 'restored-2'])
    expect(
      rows.filter((row) => row.classList.contains('msg-arriving')),
      'a restored queued row entered as new'
    ).toHaveLength(0)
  })
})
