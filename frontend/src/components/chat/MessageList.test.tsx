import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createSignal } from 'solid-js'
import { createStore } from 'solid-js/store'
import MessageList from './MessageList'
import { postChatMessageStream, setSessionId, messages } from '../../state/chat'
import type { ChatMessage } from '../../types/chat'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  postChat: vi.fn(),
  postChatStream: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-123'),
  getSessionMessages: vi.fn(),
  postFeedback: vi.fn(),
  postCorrection: vi.fn(),
  getUsers: vi.fn(),
}))

vi.mock('../../services/status', () => ({
  beginTurnStatus: vi.fn(),
  endTurnStatus: vi.fn(),
  setStreamStage: vi.fn(),
}))

describe('MessageList streamed bubble', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-sl-1')
  })

  it('renders meta that arrives after finalize (search info + extraction summary)', async () => {
    // Regression test: the streamed bubble reconciles in place (fine-grained
    // store updates); late-arriving meta must still render the learned
    // indicator and the backend badge instead of being frozen at placeholder
    // time.
    let onEvent: ((e: api.StreamEvent) => void) | undefined
    let resolveStream: (v: unknown) => void
    const streamDone = new Promise(r => { resolveStream = r })
    vi.mocked(api.postChatStream).mockImplementation(
      async (_message, _session, _files, _turn, _consent, _maxness, callback) => {
        onEvent = callback
        // The stream stays open until the server closes it (after finalize).
        return streamDone
      }
    )

    render(() => <MessageList messages={messages} />)

    const promise = postChatMessageStream('search this', 'session-sl-1')
    await Promise.resolve()

    onEvent!({ type: 'text_delta', delta: 'Here is ' })
    onEvent!({ type: 'finalize', answer: 'Here is the answer' })
    onEvent!({
      type: 'meta',
      task_type: 'search',
      extraction_summary: {
        slots_applied: 1,
        associations_created: 0,
        conflicts_created: 0,
        frame_ids: [1],
        slots: [{ frame_name: 'topic', key: 'name', value: 'testing' }],
      },
      search_extraction_summary: {
        slots_applied: 1,
        associations_created: 0,
        conflicts_created: 0,
        frame_ids: [2],
        slots: [{ frame_name: 'web', key: 'fact', value: 'found it' }],
      },
      search_info: { backend: 'brave', query: 'search this', engines: ['brave'] },
    })

    // The stream ends after the meta event rides in.
    resolveStream!({
      response: 'answer',
      task_type: 'search',
      session_id: 'session-sl-1',
    })

    const result = await promise
    expect(result.response).toBe('Here is the answer')

    // Content, learned indicator, and Brave badge all render without refresh.
    expect(document.querySelector('.msg-markdown')?.textContent).toContain('Here is the answer')
    expect(document.querySelector('.learned-indicator summary')?.textContent).toContain('Found from search')
    expect(document.querySelector('.badge-brave')?.textContent).toContain('Searched via Brave')
  })
})

describe('MessageList entrance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-in-1')
  })

  const arriving = () => [...document.querySelectorAll<HTMLElement>('.msg-arriving')]

  it('marks the row that arrives while it is on screen, and nothing already there', () => {
    // Arrival is decided once per id when <For> creates the row. A row already
    // in the store at mount was read before, so it must appear settled; a row
    // appended afterwards is the one the user watched arrive.
    const [list, setList] = createSignal<ChatMessage[]>([
      { id: 'live-seeded', role: 'assistant', content: 'already on screen' },
    ])
    render(() => <MessageList messages={list} />)

    expect(arriving(), 'a row present at mount animated').toHaveLength(0)

    setList((prev) => [...prev, { id: 'live-new', role: 'user', content: 'just typed' }])

    const marked = arriving()
    expect(marked).toHaveLength(1)
    expect(marked[0].textContent).toContain('just typed')
  })

  it('does not animate history rows that load after the mount', () => {
    // loadConversationMessages resolves after the transcript is on screen, so a
    // restored thread arrives as a live array update. The `history-` id is what
    // keeps it still; the mount-time seed alone would animate it.
    const [list, setList] = createSignal<ChatMessage[]>([])
    render(() => <MessageList messages={list} />)

    setList([
      { id: 'history-session-in-1-0', role: 'user', content: 'restored question' },
      { id: 'history-session-in-1-1', role: 'assistant', content: 'restored answer' },
    ])

    expect(document.querySelectorAll('.msg'), 'the restored rows did not render').toHaveLength(2)
    expect(arriving(), 'a restored history row entered as new').toHaveLength(0)
  })

  it('keeps one node and one marker through deltas, finalize and meta', () => {
    // Criterion 4 + 5: the streaming bubble is created empty and hidden, appears
    // on the first token with the marker, and then reconciles in place. Any
    // reactive re-decision of the class would drop the marker here.
    const [state, setState] = createStore<{ list: ChatMessage[] }>({ list: [] })
    render(() => <MessageList messages={() => state.list} />)

    setState('list', [
      { id: 'streaming-turn-1-assistant', role: 'assistant', content: '', meta: { isStreaming: true } },
    ])
    expect(document.querySelector('.msg'), 'the empty placeholder rendered').toBeNull()

    setState('list', 0, 'content', 'First ')
    const bubble = document.querySelector<HTMLElement>('.msg')
    expect(bubble, 'the first token did not render a bubble').not.toBeNull()
    expect(bubble!.classList.contains('msg-arriving')).toBe(true)

    setState('list', 0, 'content', 'First token')
    setState('list', 0, 'content', 'First token, complete.')
    expect(document.querySelector('.msg')).toBe(bubble)
    expect(arriving()).toHaveLength(1)

    setState('list', 0, 'meta', { isStreaming: false, task_type: 'search' })
    expect(document.querySelector('.msg')).toBe(bubble)
    expect(bubble!.classList.contains('msg-arriving'), 'finalize re-decided the class').toBe(true)
  })

  it('does not replay an arrival when the transcript remounts', () => {
    // ChatPage keys the transcript on the session, so switching conversations
    // remounts this list. The seed makes a remount show the thread settled
    // instead of replaying the slide for every row.
    const [list, setList] = createSignal<ChatMessage[]>([])
    const view = render(() => <MessageList messages={list} />)

    setList([{ id: 'live-1', role: 'user', content: 'hello' }])
    expect(arriving()).toHaveLength(1)

    view.unmount()
    render(() => <MessageList messages={list} />)

    expect(arriving(), 'the remount replayed the entrance').toHaveLength(0)
  })
})

describe('MessageList feedback', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-fb-1')
  })

  it('sends the current session id when reacting, not null', async () => {
    // Regression test: this used to pass a hardcoded null. The backend's first
    // parameter is a *session id* despite its legacy name, and it returns 0
    // updated slots for a falsy id -- so every thumbs up/down was a silent
    // no-op and the confidence-reinforcement loop never ran from the web UI.
    // Without the session id the agent cannot learn from reactions at all.
    const assistantMessages = () => [
      { id: 'assistant-1', role: 'assistant' as const, content: 'an answer' },
    ]

    render(() => <MessageList messages={assistantMessages} />)

    const positive = document.querySelector('[data-feedback="positive"]')
    expect(positive, 'no positive reaction control rendered').not.toBeNull()
    ;(positive as HTMLElement).click()
    await Promise.resolve()

    expect(api.postFeedback).toHaveBeenCalledTimes(1)
    const [episodeId, , kind] = vi.mocked(api.postFeedback).mock.calls[0]
    expect(episodeId, 'the session id was not sent').toBe('session-fb-1')
    expect(kind).toBe('positive')
  })
})

describe('MessageList correction', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-corr-1')
  })

  it('submits the typed correction to POST /correction with the session id and message id', async () => {
    // Regression: the correction panel's handler was an empty function, so every
    // correction was silently discarded and the pipeline never ran from the UI.
    const assistantMessages = () => [
      { id: 'assistant-1', role: 'assistant' as const, content: 'the wrong answer' },
    ]
    vi.mocked(api.postCorrection).mockResolvedValue({ status: 'ok' })

    render(() => <MessageList messages={assistantMessages} />)

    fireEvent.click(screen.getByTitle('Correct this response'))
    const textarea = screen.getByPlaceholderText(/What should I have said/i)
    fireEvent.input(textarea, { target: { value: 'the right answer' } })
    fireEvent.click(screen.getByText('Submit Correction'))
    await Promise.resolve()

    expect(api.postCorrection).toHaveBeenCalledWith(
      'session-corr-1',
      'assistant-1',
      'the right answer',
    )
  })
})

describe('MessageList source ordering', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-order-1')
  })

  it('renders the appended sources footer after the hero image', () => {
    // The backend appends "**Sources:**" to the answer text; it must end the
    // response, following the media rather than sitting between text and hero.
    const withSources = () => [
      {
        id: 'a1',
        role: 'assistant' as const,
        content:
          'Here you go.\n\n**Sources:**\n- https://a.example\n- https://b.example',
        meta: {
          search_info: {
            backend: 'brave',
            query: 'q',
            results: [
              { title: 'A', url: 'https://a.example/story-a', snippet: '', engine: 'brave', thumbnail: 'https://img/a.png' },
              { title: 'B', url: 'https://b.example/story-b', snippet: '', engine: 'brave', thumbnail: 'https://img/b.png' },
            ],
          },
        },
      },
    ]

    render(() => <MessageList messages={withSources} />)

    const hero = document.querySelector('.msg-media-hero')
    const sources = document.querySelector('.msg-sources-block')
    expect(hero, 'no hero image rendered').not.toBeNull()
    expect(sources, 'no sources block rendered').not.toBeNull()
    expect(
      hero!.compareDocumentPosition(sources!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })
})

describe('MessageList grounding', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-ground-1')
  })

  it('underlines a learned value in the answer with a confidence tooltip', () => {
    const grounded = () => [
      {
        id: 'a1',
        role: 'assistant' as const,
        content: 'Machu Picchu sits in Peru.',
        meta: {
          extraction_summary: {
            slots_applied: 1,
            associations_created: 0,
            conflicts_created: 0,
            frame_ids: [1],
            slots: [
              { frame_name: 'machu_picchu', key: 'country', value: 'Peru', confidence: 0.9 },
            ],
          },
        },
      },
    ]

    render(() => <MessageList messages={grounded} />)

    const tip = document.querySelector('.confidence-tooltip')
    expect(tip, 'no grounded annotation rendered').not.toBeNull()
    expect(tip!.textContent).toBe('Peru')
    expect(tip!.getAttribute('data-confidence')).toContain('machu_picchu.country')
    expect(tip!.getAttribute('data-confidence')).toContain('90%')
  })
})