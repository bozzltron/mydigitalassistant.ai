import { Show, createSignal, For, createEffect } from 'solid-js'
import { ChatMessage } from '../../state/chat'
import { marked } from 'marked'

// Configure marked to open links in new tabs
marked.use({
  renderer: {
    link(token) {
      const href = token.href
      const title = token.title ? ` title="${token.title}"` : ''
      return `<a href="${href}"${title} target="_blank" rel="noopener">${token.text}</a>`
    }
  }
})

interface MessageProps {
  message: ChatMessage
  onReact: (kind: 'positive' | 'negative' | 'correction', msgId: string) => void
  onCopy: (text: string) => void
  onCorrect: (msgId: string) => void
}

export default function Message(props: MessageProps) {
  const { message, onReact, onCopy, onCorrect } = props
  const isUser = message.role === 'user'
  const [showActions, setShowActions] = createSignal(false)
  const [showCorrection, setShowCorrection] = createSignal(false)
  const [correctionText, setCorrectionText] = createSignal('')
  const [contentEl, setContentEl] = createSignal<HTMLDivElement | null>(null)
  const [badgeEl, setBadgeEl] = createSignal<HTMLSpanElement | null>(null)

  const msgId = message.id || `msg-${Date.now()}`

  const handleCopy = () => {
    const plain = message.content
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[#*`_[\]]/g, '')
      .replace(/\n+/g, ' ')
      .trim()
    onCopy(plain)
  }

  const handleReact = (kind: 'positive' | 'negative' | 'correction') => {
    onReact(kind, msgId)
  }

  const handleCorrect = () => {
    if (correctionText().trim()) {
      onCorrect(msgId)
      setShowCorrection(false)
      setCorrectionText('')
    }
  }

  const extractionSummary = message.meta?.extraction_summary
  const searchExtractionSummary = message.meta?.search_extraction_summary
  const hasLearned = (!isUser) && (
    (extractionSummary?.slots && extractionSummary.slots.length > 0) ||
    (searchExtractionSummary?.slots && searchExtractionSummary.slots.length > 0)
  )

  const learnedSlots = [
    ...(extractionSummary?.slots || []),
    ...(searchExtractionSummary?.slots || [])
  ]

  const isSearch = searchExtractionSummary?.slots && searchExtractionSummary.slots.length > 0
  const conflictCount = learnedSlots.filter(s => s.conflict).length

  let learnedLabel = 'What I learned'
  if (conflictCount > 0) learnedLabel += ` (${conflictCount} auto-resolved)`
  if (isSearch) learnedLabel = 'Found from search'

  let backendBadge = ''
  if (isSearch && message.meta?.search_info) {
    const backend = message.meta.search_info.backend
    const badgeClass = backend === 'brave' ? 'badge-brave' : 'badge-searxng'
    const badgeLabel = backend === 'brave' ? 'Searched via Brave' : 'Searched via local SearXNG'
    backendBadge = `<span class="badge ${badgeClass}" style="margin-left:0.4rem;font-size:0.65rem;">${badgeLabel}</span>`
  }

  createEffect(() => {
    if (contentEl()) {
      contentEl()!.innerHTML = marked.parse(message.content || '') as string
    }
  })

  createEffect(() => {
    if (badgeEl() && backendBadge) {
      badgeEl()!.innerHTML = backendBadge
    }
  })

  return (
    <div 
      class={`msg ${isUser ? 'msg-user' : 'msg-assistant'}`}
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      <div class="content">
        <div ref={setContentEl} />
        
        {message.meta?.task_type && (
          <div class="msg-meta">
            type: {message.meta.task_type}
          </div>
        )}

        {message.meta?.ogData && message.meta.task_type === 'search' && (
          <Show when={Object.entries(message.meta.ogData).some(([, d]) => d && d.image)}>
            <div class="msg-images">
              <For each={Object.entries(message.meta.ogData)
                .filter(([, d]) => d && d.image)}>{([url, data]) => {
                  const siteName = data.site_name || new URL(url).hostname
                  return (
                    <a href={url} target="_blank" rel="noopener" >
                      <img src={data.image} alt="" loading="lazy" onError={(e) => { e.currentTarget.remove() }} />
                      <div class="img-site">{siteName}</div>
                    </a>
                  )
                }}</For>
            </div>
          </Show>
        )}

        {!isUser && hasLearned && (
          <details class="learned-indicator">
            <summary>{learnedLabel}{backendBadge && <span ref={setBadgeEl} />}</summary>
<div class="learned-items">
              <For each={learnedSlots}>
                {(slot) => (
                  <div 
                     
                    class={`learned-item ${slot.conflict ? 'kind-conflict' : isSearch ? 'kind-search' : 'kind-learned'}`}
                  >
                    {slot.conflict 
                      ? `Auto-resolved: ${slot.frame_name} → ${slot.key}: ${slot.value}`
                      : `${slot.frame_name} → ${slot.key}: ${slot.value}`}
                  </div>
                )}
              </For>
            </div>
          </details>
        )}
      </div>

      <div class="msg-actions" style={{ opacity: showActions() ? 1 : 0 }}>
        <button 
          class="msg-action-btn" 
          title="Copy message"
          onClick={handleCopy}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
            <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
          </svg>
        </button>

        {!isUser && (
          <>
            <button 
              class="reaction-btn" 
              title="This was good"
              onClick={() => handleReact('positive')}
            >
              👍
            </button>
            <button 
              class="reaction-btn" 
              title="This was bad"
              onClick={() => handleReact('negative')}
            >
              👎
            </button>
            <button 
              class="reaction-btn" 
              title="Correct this response"
              onClick={() => setShowCorrection(true)}
            >
              ✏️
            </button>
          </>
        )}

        {showCorrection() && (
          <div class="correction-panel" style={{ "margin-top": '0.5rem' }}>
            <textarea 
              placeholder="What should I have said? Or what do you want to correct?"
              value={correctionText()}
              onInput={(e) => setCorrectionText(e.target.value)}
              style={{ 
                width: '100%', 
                background: 'var(--bg)', 
                border: '1px solid var(--border)', 
                "border-radius": '6px', 
                color: 'var(--text)', 
                padding: '0.6rem 0.75rem', 
                "font-family": 'inherit', 
                "font-size": '0.95rem', 
                resize: 'none', 
                "min-height": '64px', 
                "line-height": '1.6', 
                "margin-bottom": '0.5rem' 
              }}
            />
            <div class="correction-actions" style={{ display: 'flex', gap: '0.5rem', "justify-content": 'flex-end' }}>
              <button 
                class="correction-cancel" 
                onClick={() => { setShowCorrection(false); setCorrectionText('') }}
                style={{ 
                  background: 'var(--surface)', 
                  border: '1px solid var(--border)', 
                  color: '#fff', 
                  "border-radius": '6px', 
                  padding: '0.4rem 0.8rem', 
                  "font-size": '0.8rem' 
                }}
              >
                Cancel
              </button>
              <button 
                class="correction-submit" 
                onClick={handleCorrect}
                style={{ 
                  background: 'var(--accent)', 
                  border: 'none', 
                  color: '#fff', 
                  "border-radius": '6px', 
                  padding: '0.4rem 0.8rem', 
                  "font-size": '0.8rem', 
                  "font-weight": 500 
                }}
              >
                Submit Correction
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}