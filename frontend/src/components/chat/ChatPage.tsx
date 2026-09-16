import { createSignal, createEffect, Show, For, onMount, onCleanup } from 'solid-js'
import MessageList from './MessageList'
import InputBar from './InputBar'
import StatusIndicator from './StatusIndicator'
import { messages, sessionId, setSessionId, isTurnActive, useConversationTurnId, addMessageToConversation, enqueueMessage, removeQueuedMessage, queue } from '../../state/chat'
import { Session } from '../../state/session'
import { useTurnStatus } from '../../services/status'
import { useVoiceRecording } from '../../hooks/useVoiceRecording'
import { voice } from '../../state/voice'
import { settings } from '../../state/settings'
import type {
  ExtractionSummary,
  SearchInfo,
} from '../../types'

// Wrapper component that calls useTurnStatus with a dynamic turnId
function StatusWrapper(props: { turnId: () => string | undefined }) {
  const { turnStatus, isPolling } = useTurnStatus(props.turnId)
  return <StatusIndicator turnStatus={turnStatus} isPolling={isPolling} />
}

interface SendMessageResult {
  response: string
  task_type?: string
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
  session_id?: string
}

export default function ChatPage(props: {
  conversation: Session | null
  sendMessage: (message: string, session_id?: string, attached_files?: File[]) => Promise<SendMessageResult>
}) {
  const [showTrace, setShowTrace] = createSignal(false)
  const [messagesContainerRef, setMessagesContainerRef] = createSignal<HTMLDivElement | null>(null)
  const [isDictating, setIsDictating] = createSignal(false)
  
  const isSending = isTurnActive

  // Get the current conversation's turnId
  const currentConvTurnId = useConversationTurnId(sessionId)

  // Auto-scroll to bottom when messages change
  let scrollTimeout: number | null = null
  createEffect(() => {
    messages()
    queue()
    if (scrollTimeout) {
      clearTimeout(scrollTimeout)
    }
    scrollTimeout = window.setTimeout(() => {
      const container = messagesContainerRef()
      if (container) {
        container.scrollTop = container.scrollHeight
      }
    }, 0)
  })

  onCleanup(() => {
    if (scrollTimeout) {
      clearTimeout(scrollTimeout)
    }
  })

  // Load user ID on mount
  onMount(async () => {
    try {
      await fetch('/users')
    } catch (e) {
      console.error('Failed to load user:', e)
    }
  })

  const handleSendMessage = async (message: string) => {
    if (!message.trim()) return

    const currentSessionId = sessionId()
    if (!currentSessionId) return

    // If a turn is active, queue the message instead of sending immediately
    if (isSending()) {
      enqueueMessage(message.trim())
      return
    }

    try {
      const userMessage = {
        role: 'user' as const,
        content: message,
        id: Date.now().toString()
      }

      addMessageToConversation(currentSessionId, userMessage)

      const result = await props.sendMessage(message, currentSessionId)

      if (result.session_id) {
        setSessionId(result.session_id)
        localStorage.setItem('session_id', result.session_id)
      }

      const assistantMessage = {
        role: 'assistant' as const,
        content: result.response,
        id: Date.now().toString() + '-assistant',
        meta: {
          task_type: result.task_type,
          extraction_summary: result.extraction_summary,
          search_extraction_summary: result.search_extraction_summary,
          search_info: result.search_info,
        }
      }

      addMessageToConversation(currentSessionId, assistantMessage)
    } catch (error) {
      console.error('Error sending message:', error)
      const errorMessage = {
        role: 'assistant' as const,
        content: 'Error: Failed to send message',
        id: Date.now().toString() + '-error'
      }
      addMessageToConversation(currentSessionId, errorMessage)
    }
  }

  // TTS: speak assistant responses when they arrive
  const [isTtsSpeaking, setIsTtsSpeaking] = createSignal(false)
  
  // Load spoken message IDs from localStorage
  const [spokenMsgIds, setSpokenMsgIds] = createSignal<Set<string>>(new Set())
  onMount(() => {
    try {
      const saved = localStorage.getItem('tts_spoken_messages')
      if (saved) {
        setSpokenMsgIds(new Set(JSON.parse(saved)))
      }
    } catch (e) {
      console.warn('Failed to load spoken messages:', e)
    }
  })

  const markAsSpoken = (msgId: string) => {
    setSpokenMsgIds(prev => {
      const next = new Set(prev)
      next.add(msgId)
      try {
        localStorage.setItem('tts_spoken_messages', JSON.stringify([...next]))
      } catch (e) {
        console.warn('Failed to save spoken messages:', e)
      }
      return next
    })
  }

  createEffect(() => {
    const msgs = messages()
    const lastMsg = msgs[msgs.length - 1]
    if (lastMsg && lastMsg.role === 'assistant' && !spokenMsgIds().has(lastMsg.id)) {
      markAsSpoken(lastMsg.id)
      if (settings.ttsEnabled && 'speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(lastMsg.content)
        const voiceUri = settings.voiceUri
        if (voiceUri) {
          const voices = speechSynthesis.getVoices()
          const selectedVoice = voices.find(v => v.voiceURI === voiceUri)
          if (selectedVoice) utterance.voice = selectedVoice
        }
        utterance.rate = settings.voiceSpeed
        utterance.pitch = settings.voicePitch
        utterance.volume = settings.voiceVolume
        utterance.onstart = () => setIsTtsSpeaking(true)
        utterance.onend = () => setIsTtsSpeaking(false)
        utterance.onerror = () => setIsTtsSpeaking(false)
        speechSynthesis.speak(utterance)
      }
    }
  })

  const stopSpeaking = () => {
    if ('speechSynthesis' in window) {
      speechSynthesis.cancel()
      setIsTtsSpeaking(false)
    }
  }

  // Voice recording hook - runs when voice mode is active
  useVoiceRecording({
    isVoiceMode: () => voice.status === 'listening' || voice.status === 'processing' || voice.status === 'speaking',
    isDictationMode: () => isDictating(),
    onTranscription: handleSendMessage,
  })

  const handleRemoveQueued = (id: string) => {
    removeQueuedMessage(id)
  }

  const handleDictationStart = () => {
    setIsDictating(true)
    startDictation()
  }

  const handleDictationStop = () => {
    setIsDictating(false)
    endDictation()
  }

  return (
    <div id="main">
      <div id="chat-area" class="chat-area">
        <div id="messages" ref={setMessagesContainerRef}>
          <MessageList messages={messages()} />
          
          <StatusWrapper turnId={currentConvTurnId} />
          
          <Show when={queue().length > 0}>
            <For each={queue()}>
              {(queuedMsg) => (
                <div class="msg msg-user msg-queued" id={queuedMsg.id}>
                  <div class="content">{queuedMsg.message}</div>
                  <button 
                    class="queued-remove"
                    onClick={() => handleRemoveQueued(queuedMsg.id)}
                    title="Remove queued message"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
                      <path d="M18 6 6 18M6 6l12 12"/>
                    </svg>
                  </button>
                </div>
              )}
            </For>
          </Show>

          <Show when={messages().length === 0 && queue().length === 0}>
            <div class="welcome-message" id="welcome">
              <div class="welcome-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
              <h2>{props.conversation?.title ? `Conversation: ${props.conversation.title}` : 'Ready to chat'}</h2>
              <p>Type a message below or click Voice to start hands-free.</p>
            </div>
          </Show>
        </div>

        <div id="input-row">
          <InputBar 
            onSend={handleSendMessage} 
            isSending={isSending()}
            isDictating={isDictating()}
            onDictationStart={handleDictationStart}
            onDictationStop={handleDictationStop}
          />
        </div>
      </div>

      <Show when={isTtsSpeaking()}>
        <button 
          class="stop-speaking-btn"
          id="stop-speaking-btn"
          onClick={stopSpeaking}
          title="Stop speaking"
          style={{
            position: 'fixed',
            bottom: '90px',
            right: '20px',
            zIndex: 100,
            padding: '0.75rem 1rem',
            background: 'var(--error)',
            color: 'white',
            border: 'none',
            borderRadius: '8px',
            fontSize: '0.875rem',
            fontWeight: 600,
            cursor: 'pointer',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem'
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="6" y="4" width="4" height="16" rx="1"/>
            <rect x="14" y="4" width="4" height="16" rx="1"/>
          </svg>
          Stop
        </button>
      </Show>

      <div id="trace-panel" class={`trace-panel ${showTrace() ? '' : 'hidden'}`}>
        <div class="trace-header">
          <span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style={{"vertical-align":"middle","margin-right":"4px"}}><line x1="18" x2="18" y1="20" y2="10"/><line x1="12" x2="12" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="14"/></svg>
            Trace
          </span>
          <button 
            class="trace-close" 
            onClick={() => setShowTrace(false)}
            id="trace-close"
          >&times;</button>
        </div>
        <div class="trace-content">
          <div class="trace-section">
            <div class="trace-label">Task type</div>
            <div class="trace-value" id="trace-task-type">-</div>
          </div>
          <div class="trace-section">
            <div class="trace-label">Memory context</div>
            <div class="trace-value" id="trace-memory">-</div>
          </div>
          <div class="trace-section">
            <div class="trace-label">Citations</div>
            <div class="trace-value" id="trace-citations">-</div>
          </div>
          <div class="trace-section" id="trace-search-section" style={{"display":"none"}}>
            <div class="trace-label">Search</div>
            <div class="trace-value" id="trace-search-info">-</div>
          </div>
        </div>
      </div>
    </div>
  )
}