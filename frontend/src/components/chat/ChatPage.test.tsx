import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import ChatPage from './ChatPage'
import {
  setSessionId,
  setIsStreaming,
  setStreamingMessageId,
} from '../../state/chat'
import {
  clearQueue,
  enqueue,
  setProcessing,
  setQueueState,
} from '../../state/messageQueue'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  postChat: vi.fn(),
  postChatStream: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-123'),
  getSessionMessages: vi.fn(),
  postFeedback: vi.fn(),
  getUsers: vi.fn(),
  postFileUpload: vi.fn(),
}))

vi.mock('../../services/status', () => ({
  useTurnStatus: () => ({ turnStatus: () => null, isPolling: () => false }),
  beginTurnStatus: vi.fn(),
  endTurnStatus: vi.fn(),
  setStreamStage: vi.fn(),
  getStageLabel: (stage: string, detail?: string) => detail || stage,
}))

vi.mock('../../hooks/useVoiceRecording', () => ({
  useVoiceRecording: () => ({ startDictation: vi.fn(), endDictation: vi.fn() }),
}))

// ChatPage's TTS effect constructs an utterance when an assistant bubble lands;
// test-setup stubs speechSynthesis but not its constructor, so define it here.
class MockSpeechSynthesisUtterance {
  text: string
  voice: unknown = null
  rate = 1
  pitch = 1
  volume = 1
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(text: string) {
    this.text = text
  }
}
Object.defineProperty(window, 'SpeechSynthesisUtterance', {
  writable: true,
  configurable: true,
  value: MockSpeechSynthesisUtterance,
})

describe('ChatPage queue entrance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    clearQueue()
    setProcessing(false)
    setSessionId('session-q-1')
    setIsStreaming(false)
    setStreamingMessageId(null)
  })

  // The queue store is module state shared with every other test in this file:
  // leave it empty so the following describes start clean.
  afterEach(() => {
    clearQueue()
    setProcessing(false)
  })

  const queuedRows = () => [...document.querySelectorAll<HTMLElement>('.msg-queued')]
  const marked = () => queuedRows().filter((row) => row.classList.contains('msg-arriving'))

  it('marks a live enqueue and leaves the rows already queued unmarked', () => {
    // The queue restored from the saved conversation is loaded *before* ChatPage
    // seeds the arrival set. Seeding first would leave them unseen, and every
    // restore would slide rows in as if the user had just typed them.
    setQueueState({
      queue: [
        { id: 'saved-1', content: 'queued before the reload', source: 'text', timestamp: 1 },
        { id: 'saved-2', content: 'and this one too', source: 'text', timestamp: 2 },
      ],
    })
    // Messages leave the queue as they are sent; hold it processing so the rows
    // stay put long enough to inspect.
    setProcessing(true)

    render(() => <ChatPage conversation={null} />)

    expect(queuedRows().map((row) => row.id)).toEqual(['saved-1', 'saved-2'])
    expect(marked(), 'a restored queued row entered as new').toHaveLength(0)

    enqueue({ content: 'typed while it was busy', source: 'text', timestamp: 3 })

    expect(marked()).toHaveLength(1)
    expect(marked()[0].textContent).toContain('typed while it was busy')
  })

  it('does not restart the entrance when another queued row is removed', async () => {
    // Criterion 2: the marker is decided once per id, at row creation. Deriving
    // it from the queue array would re-evaluate every surviving row on a dequeue.
    // The removal goes through the row's own control, so the button is covered
    // too (criterion 7).
    setProcessing(true)
    render(() => <ChatPage conversation={null} />)

    const firstId = enqueue({ content: 'first queued', source: 'text', timestamp: 1 })
    const secondId = enqueue({ content: 'second queued', source: 'text', timestamp: 2 })

    const first = document.getElementById(firstId)
    const second = document.getElementById(secondId)
    expect(first?.classList.contains('msg-arriving')).toBe(true)
    expect(second?.classList.contains('msg-arriving')).toBe(true)

    first!.querySelector<HTMLButtonElement>('.queued-remove')!.click()
    await Promise.resolve()

    expect(document.getElementById(firstId), 'the removed row is still rendered').toBeNull()
    const survivor = document.getElementById(secondId)
    expect(survivor, 'the remaining row was recreated').toBe(second)
    expect(survivor!.classList.contains('msg-arriving')).toBe(true)
  })

  it('does not re-mark a row that is still queued when the session switches', async () => {
    // The panel lives inside the session-keyed <Show>, so switching conversations
    // remounts it. The set lives above that boundary, which is the only reason a
    // surviving row does not slide in again.
    setProcessing(true)
    render(() => <ChatPage conversation={null} />)

    const id = enqueue({ content: 'still waiting', source: 'text', timestamp: 1 })
    const before = document.getElementById(id)
    expect(before?.classList.contains('msg-arriving')).toBe(true)

    setSessionId('session-q-2')
    await vi.waitFor(() => expect(document.getElementById(id), 'the panel did not remount').not.toBeNull())

    // The premise of this test: the row really was recreated by the session
    // switch. Without this, a green result could come from no remount at all.
    expect(document.getElementById(id), 'the row was not recreated by the switch').not.toBe(before)
    expect(document.getElementById(id)!.classList.contains('msg-arriving')).toBe(false)
  })
})

describe('ChatPage chat wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-cp-1')
    setIsStreaming(false)
    setStreamingMessageId(null)
  })

  it('sends through the streaming endpoint and renders the finalize answer', () => {
    // Regression test: previously ChatPage destructured `sendMessage:
    // sendMessageStream`, which bound the NON-streaming postChat (POST /chat)
    // to the streaming slot. That path never adds the assistant reply to the
    // store, so answers only appeared after a page refresh.
    let onEvent: ((e: api.StreamEvent) => void) | undefined
    vi.mocked(api.postChatStream).mockImplementation(
      async (_message, _session, _files, _turn, _consent, _maxness, callback) => {
        onEvent = callback
        return { response: 'Streamed reply', task_type: 'functional', session_id: 'session-cp-1' }
      }
    )

    render(() => <ChatPage conversation={null} />)

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Hello' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    // The streaming endpoint must be used, never the non-streaming one.
    expect(api.postChatStream).toHaveBeenCalled()
    expect(api.postChat).not.toHaveBeenCalled()

    // The bubbled answer renders without a refresh.
    onEvent!({ type: 'finalize', answer: 'Streamed reply' })
    const markdown = document.querySelector('.msg-assistant .msg-markdown')
    expect(markdown?.textContent).toContain('Streamed reply')
  })

  it('speaks the finalized assistant reply when read-aloud is on', async () => {
    let onEvent: ((e: api.StreamEvent) => void) | undefined
    vi.mocked(api.postChatStream).mockImplementation(
      async (_message, _session, _files, _turn, _consent, _maxness, callback) => {
        onEvent = callback
        return { response: 'Streamed reply', task_type: 'functional', session_id: 'session-cp-1' }
      }
    )

    render(() => <ChatPage conversation={null} />)

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Hello' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    await vi.waitFor(() => expect(onEvent).toBeDefined())
    onEvent!({ type: 'finalize', answer: 'Streamed reply' })

    await vi.waitFor(() =>
      expect(window.speechSynthesis.speak).toHaveBeenCalled()
    )
    const spoken = vi.mocked(window.speechSynthesis.speak).mock.calls.at(-1)![0] as unknown as {
      text: string
    }
    expect(spoken.text).toBe('Streamed reply')
  })

  it('sends the message even when an attachment fails to upload', async () => {
    // The input bar uploads bytes through the multipart endpoint and references
    // the stored file by frame. If that upload throws — the backend 400s an
    // unsupported type, or the request drops — the user's typed message must
    // still go: losing what they wrote because a file was rejected is the worse
    // failure. The failed attachment is dropped instead.
    vi.mocked(api.postChatStream).mockResolvedValue({
      response: 'ok',
      task_type: 'functional',
      session_id: 'session-cp-1',
    })
    vi.mocked(api.postFileUpload).mockRejectedValue(new Error('Unsupported file type'))

    render(() => <ChatPage conversation={null} />)

    const fileInput = screen.getByTestId('file-input') as HTMLInputElement
    const file = new File(['%PDF-1.4'], 'report.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'read this please' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    // The upload was attempted, failed, and did not stop the message.
    await vi.waitFor(() => expect(api.postChatStream).toHaveBeenCalled())
    const [message, , attached] = vi.mocked(api.postChatStream).mock.calls[0]
    expect(message).toBe('read this please')
    expect(attached).toEqual([])
  })

  it('shows Max as pressed when it was already on', () => {
    // Regression: the button toggled an `active` class that no stylesheet defined,
    // and its state died on reload, so it read as broken. Both are pinned here.
    localStorage.setItem('max_intelligence', 'on')
    render(() => <ChatPage conversation={null} />)

    const button = screen.getByRole('button', { name: 'Toggle max intelligence' })
    expect(button).toHaveAttribute('aria-pressed', 'true')
  })

  it('remembers Max across a reload', () => {
    render(() => <ChatPage conversation={null} />)

    const button = screen.getByRole('button', { name: 'Toggle max intelligence' })
    expect(button).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(button)

    expect(button).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('max_intelligence')).toBe('on')
  })
})