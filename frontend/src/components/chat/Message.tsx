import { createSignal, For, createMemo, createEffect, ErrorBoundary } from 'solid-js'
import type { JSX } from 'solid-js'
import type { ChatMessage } from '../../types/chat'
import MessageContent from './MessageContent'

interface MessageProps {
  message: ChatMessage
  onReact: (kind: 'positive' | 'negative' | 'correction', msgId: string) => void
  onCopy: (text: string) => void
  /**
   * Submits a natural-language correction for this message. Resolves `true` when
   * the backend accepted it, so the panel can show inline feedback.
   */
  onCorrect: (msgId: string, text: string) => Promise<boolean> | boolean
}

export default function Message(props: MessageProps) {
  const message = createMemo(() => props.message)
  const getOnReact = () => props.onReact
  const getOnCopy = () => props.onCopy
  const getOnCorrect = () => props.onCorrect
  const isUser = createMemo(() => message().role === 'user')
  const [showCorrection, setShowCorrection] = createSignal(false)
  const [correctionText, setCorrectionText] = createSignal('')
  const [correctionPending, setCorrectionPending] = createSignal(false)
  const [correctionStatus, setCorrectionStatus] = createSignal<string | null>(null)

  // Message ids are stable row keys (history-* / streaming-*), so reading the
  // id once is safe: the row is updated in place through the chat store, so
  // <For> reconciles it rather than recreating it.
  const msgId = createMemo(() => props.message.id || `msg-${Date.now()}`)

  const handleCopy = () => {
    const plain = message().content
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[#*`_[\]]/g, '')
      .replace(/\n+/g, ' ')
      .trim()
    getOnCopy()(plain)
  }

  const handleReact = (kind: 'positive' | 'negative' | 'correction') => {
    getOnReact()(kind, msgId())
  }

  const handleCorrect = async () => {
    const text = correctionText().trim()
    if (!text || correctionPending()) return
    setCorrectionPending(true)
    setCorrectionStatus(null)
    try {
      const applied = await getOnCorrect()(msgId(), text)
      if (applied) {
        setShowCorrection(false)
        setCorrectionText('')
        setCorrectionStatus('Correction sent')
      } else {
        setCorrectionStatus('Could not apply the correction — try rephrasing.')
      }
    } finally {
      setCorrectionPending(false)
    }
  }

  // Derived values must be reactive memos (not one-shot consts): the streamed
  // bubble reconciles in place across text_delta/finalize/meta, so late-arriving
  // search info and extraction summaries need to re-render.
  const extractionSummary = createMemo(() => message().meta?.extraction_summary)
  const searchExtractionSummary = createMemo(() => message().meta?.search_extraction_summary)
  const learnedSlots = createMemo(() => [
    ...(extractionSummary()?.slots || []),
    ...(searchExtractionSummary()?.slots || [])
  ])
  const hasLearned = createMemo(() => (!isUser() && (
    (extractionSummary()?.slots && extractionSummary()!.slots.length > 0) ||
    (searchExtractionSummary()?.slots && searchExtractionSummary()!.slots.length > 0)
  )))

  const isSearch = createMemo(() =>
    searchExtractionSummary()?.slots && searchExtractionSummary()!.slots.length > 0)
  const conflictCount = createMemo(() => learnedSlots().filter(s => s.conflict).length)

  // The turn's context cost, if the tool loop reported one. A percentage of the
  // window is the readable number; the tokens are the detail. `truncated` is the
  // signal that matters -- the prompt reached the window, so content may have
  // been dropped.
  const contextPct = createMemo(() => {
    const m = message().meta
    if (!m?.context_window || !m.prompt_tokens) return 0
    return Math.round((m.prompt_tokens / m.context_window) * 100)
  })
  const contextTruncated = createMemo(() => {
    const m = message().meta
    return !!m?.context_window && (m.prompt_tokens || 0) >= m.context_window
  })
  const formatTokens = (n?: number) =>
    n && n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n ?? 0}`

  const learnedLabel = createMemo(() => {
    let label = 'What I learned'
    if (conflictCount() > 0) label += ` (${conflictCount()} auto-resolved)`
    if (isSearch()) label = 'Found from search'
    return label
  })

  const [backendBadge, setBackendBadge] = createSignal<JSX.Element | null>(null)

  createEffect(() => {
    const info = message().meta?.search_info
    if (isSearch() && info) {
      const badgeClass = info.backend === 'brave' ? 'badge-brave' : 'badge-searxng'
      const badgeLabel = info.backend === 'brave' ? 'Searched via Brave' : 'Searched via local SearXNG'
      setBackendBadge(<span class={`badge ${badgeClass}`}>{badgeLabel}</span>)
    } else {
      setBackendBadge(null)
    }
  })

  return (
    <div
      class={`msg ${isUser() ? 'msg-user' : 'msg-assistant'}`}
    >
      <div class="content">
        {/* A throw while rendering one message (bad markdown, a malformed
            media URL) must not take down the whole transcript. */}
        <ErrorBoundary fallback={<div class="msg-error">This message could not be rendered.</div>}>
          <MessageContent message={message} />
        </ErrorBoundary>

        {message().meta?.task_type && (
          <div class="msg-meta">
            type: {message().meta!.task_type}
          </div>
        )}

        {message().meta?.context_window ? (
          <div
            class={`msg-meta msg-context${contextTruncated() ? ' is-truncated' : ''}`}
            title={
              contextTruncated()
                ? 'This turn filled the model context window, so some content may have been dropped.'
                : 'How much of the model context window this turn used.'
            }
          >
            Context {contextPct()}% · {formatTokens(message().meta!.prompt_tokens)}/
            {formatTokens(message().meta!.context_window)}
          </div>
        ) : null}

        {!isUser() && hasLearned() && (
          <details class="learned-indicator">
            <summary>{learnedLabel()}{backendBadge()}</summary>
            <div class="learned-items">
              <For each={learnedSlots()}>
                {(slot) => (
                  <div
                    class={`learned-item ${slot.conflict ? 'kind-conflict' : isSearch() ? 'kind-search' : 'kind-learned'}`}
                  >
                    {slot.conflict
                      ? `Auto-resolved: ${slot.frame_name} \u2192 ${slot.key}: ${slot.value}`
                      : `${slot.frame_name} \u2192 ${slot.key}: ${slot.value}`}
                  </div>
                )}
              </For>
            </div>
          </details>
        )}

      </div>

      <div class="msg-footer">
        <div class="msg-actions">
        <button
          class="msg-action-btn"
          title="Copy message"
          onClick={handleCopy}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
            <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
          </svg>
        </button>

        {!isUser() && (
          <>
            <button
              class="reaction-btn"
              data-feedback="positive"
              title="This was good"
              onClick={() => handleReact('positive')}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M7 10v12" />
                <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
              </svg>
            </button>
            <button
              class="reaction-btn"
              data-feedback="negative"
              title="This was bad"
              onClick={() => handleReact('negative')}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M17 14V2" />
                <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
              </svg>
            </button>
            <button
              class="reaction-btn"
              title="Correct this response"
              onClick={() => { setShowCorrection(true); setCorrectionStatus(null) }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
                <line x1="4" x2="4" y1="22" y2="15" />
              </svg>
            </button>
          </>
        )}
        </div>
      </div>

      {showCorrection() && (
          <div class="correction-panel">
            <textarea
              placeholder="What should I have said? Or what do you want to correct?"
              value={correctionText()}
              onInput={(e) => setCorrectionText(e.target.value)}
              class="correction-textarea"
            />
            <div class="correction-actions">
              <button
                class="correction-cancel"
                onClick={() => { setShowCorrection(false); setCorrectionText(''); setCorrectionStatus(null) }}
              >
                Cancel
              </button>
              <button
                class="correction-submit"
                onClick={handleCorrect}
                disabled={correctionPending() || !correctionText().trim()}
              >
                {correctionPending() ? 'Sending…' : 'Submit Correction'}
              </button>
            </div>
            {correctionStatus() && (
              <div class="correction-status" role="status">{correctionStatus()}</div>
            )}
          </div>
        )}
    </div>
  )
}