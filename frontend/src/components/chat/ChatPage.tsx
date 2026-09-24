import { createSignal, createEffect, Show, For, onMount, onCleanup } from 'solid-js'
import MessageList from './MessageList'
import InputBar from './InputBar'
import StatusIndicator from './StatusIndicator'
import { Modal } from '../ui/Modal'
import { messages, sessionId, setSessionId, isTurnActive, useConversationTurnId, addMessageToConversation, enqueueMessage, removeQueuedMessage, queue, isStreaming } from '../../state/chat'
import { Session } from '../../state/session'
import { useTurnStatus } from '../../services/status'
import { useVoiceRecording } from '../../hooks/useVoiceRecording'
import { useChat } from '../../hooks/useChat'
import { voice, setTtsSpeaking } from '../../state/voice'
import { settings } from '../../state/settings'
import type {
  SearchInfo,
  AttachedFile,
} from '../../types'

// Wrapper component that calls useTurnStatus with a dynamic turnId
function StatusWrapper(props: { turnId: () => string | undefined }) {
  const { turnStatus, isPolling } = useTurnStatus(props.turnId)
  return <StatusIndicator turnStatus={turnStatus} isPolling={isPolling} />
}

export default function ChatPage(props: {
  conversation: Session | null
}) {
  const { sendMessageStream } = useChat()
  const [showTrace, setShowTrace] = createSignal(false)
  const [messagesContainerRef, setMessagesContainerRef] = createSignal<HTMLDivElement | null>(null)
  const [isDictating, setIsDictating] = createSignal(false)
  const [maxIntelligence, setMaxIntelligence] = createSignal(false)
  const [pendingSearchConsent, setPendingSearchConsent] = createSignal<{
    message: string
    attachedFiles: AttachedFile[]
    searchInfo: SearchInfo
  } | null>(null)
  
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

  // Read file and convert to AttachedFile format
  const readFileAsAttachedFile = async (file: File): Promise<AttachedFile> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const text = e.target?.result as string || ''
        const ext = file.name.split('.').pop()?.toLowerCase() || 'txt'
        resolve({
          name: file.name,
          ext,
          preview: text.slice(0, 500),
          content: text,
          text,
          key_entities: [],
          open_questions: [],
        })
      }
      reader.onerror = reject
      reader.readAsText(file)
    })
  }

  const handleSendMessage = async (message: string, attachedFiles?: File[], search_consent?: boolean) => {
    if (!message.trim()) return

    const currentSessionId = sessionId()
    if (!currentSessionId) return

    // If a turn is active for THIS conversation, queue the message instead of sending immediately
    if (isSending()) {
      enqueueMessage(message.trim(), currentSessionId)
      return
    }

    // Process attached files
    let processedFiles: AttachedFile[] = []
    if (attachedFiles && attachedFiles.length > 0) {
      processedFiles = await Promise.all(attachedFiles.map(readFileAsAttachedFile))
    }

    try {
      const userMessage = {
        role: 'user' as const,
        content: message,
        id: Date.now().toString()
      }

      addMessageToConversation(currentSessionId, userMessage)

      const result = await sendMessageStream(message, currentSessionId, processedFiles, search_consent, maxIntelligence())

      if (result.session_id) {
        setSessionId(result.session_id)
        localStorage.setItem('session_id', result.session_id)
      }

      // Search consent rides the stream via the meta event: the backend emits
      // task_type='search_consent_required' + search_info before the finalize.
      // The consent question itself is already rendered by the streamed bubble.
      if (result.task_type === 'search_consent_required' && result.search_info) {
        setPendingSearchConsent({
          message,
          attachedFiles: processedFiles,
          searchInfo: result.search_info,
        })
        return
      }

      // Note: With streaming, the assistant message is added during streaming in postChatMessageStream
      // The final message update is handled by the streaming callback
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

  const handleConsentProceed = async () => {
    const consent = pendingSearchConsent()
    if (!consent) return
    setPendingSearchConsent(null)
    await handleSendMessage(consent.message, undefined, true)
  }

  const handleConsentCancel = () => {
    const consent = pendingSearchConsent()
    if (!consent) return
    setPendingSearchConsent(null)
    // Add a cancellation message
    const currentSessionId = sessionId()
    if (currentSessionId) {
      const cancelMessage = {
        role: 'assistant' as const,
        content: 'Search cancelled. Your query was not sent to Brave.',
        id: Date.now().toString() + '-assistant',
        meta: { task_type: 'search_cancelled' }
      }
      addMessageToConversation(currentSessionId, cancelMessage)
    }
  }

  // TTS: speak assistant responses when they arrive (only finalized, non-streaming)
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
    // Only speak newly generated assistant messages that are FINALIZED (not streaming)
    // Historical messages have IDs starting with "history-"
    // Streaming messages have meta.isStreaming === true - wait for finalize
    if (
      lastMsg &&
      lastMsg.role === 'assistant' &&
      !spokenMsgIds().has(lastMsg.id) &&
      !lastMsg.meta?.isStreaming
    ) {
      // Skip historical messages - they were already spoken in their original conversation
      if (lastMsg.id.startsWith('history-')) {
        markAsSpoken(lastMsg.id)
        return
      }
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
        utterance.onstart = () => setTtsSpeaking(true)
        utterance.onend = () => setTtsSpeaking(false)
        utterance.onerror = () => setTtsSpeaking(false)
        speechSynthesis.speak(utterance)
      }
    }
  })

  // Voice recording hook - runs when voice mode is active
  // Note: voice.status === 'speaking' is for voice mode's own speaking state.
  // TTS speaking is tracked separately via isTtsSpeaking() in the hook.
  // We also check isTurnActive to avoid restarting listening during an active chat turn.
  useVoiceRecording({
    isVoiceMode: () => voice.status === 'listening' || voice.status === 'processing',
    isDictationMode: () => isDictating(),
    isTurnActive: () => isTurnActive(),
    onTranscription: handleSendMessage,
  })

  const handleRemoveQueued = (id: string) => {
    removeQueuedMessage(id, sessionId())
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
          <MessageList messages={messages} />
          
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
            isStreaming={isStreaming()}
            isDictating={isDictating()}
            onDictationStart={handleDictationStart}
            onDictationStop={handleDictationStop}
            maxEnabled={maxIntelligence()}
            onToggleMax={() => setMaxIntelligence((v) => !v)}
          />
        </div>
      </div>

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

      {/* Search Consent Modal */}
      <Show when={pendingSearchConsent()}>
        <Modal
          isOpen={true}
          onClose={handleConsentCancel}
          title="Search Consent Required"
          size="medium"
        >
          <div class="consent-modal-content">
            <div class="consent-warning">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                <line x1="12" x2="12" y1="9" y2="13"/>
                <line x1="12" x2="12.01" y1="17" y2="17"/>
              </svg>
            </div>
            <h3>Sensitive Search Query</h3>
            <p>This search query may contain sensitive information that would be sent to Brave's servers.</p>
            
            <Show when={pendingSearchConsent()?.searchInfo.sensitivity}>
              {() => {
                const sensitivity = pendingSearchConsent()!.searchInfo.sensitivity
                return (
                  <div class="consent-details">
                    <div class="consent-level">
                      <span class={`consent-badge ${sensitivity.level}`}>{sensitivity.level}</span>
                      <span>{sensitivity.reason}</span>
                    </div>
                    <Show when={sensitivity.categories.length > 0}>
                      <div class="consent-categories">
                        <strong>Categories:</strong>
                        <ul>
                          <For each={sensitivity.categories}>
                            {(cat) => <li>{cat}</li>}
                          </For>
                        </ul>
                      </div>
                    </Show>
                  </div>
                )
              }}
            </Show>

            <div class="consent-query">
              <strong>Query:</strong>
              <code>{pendingSearchConsent()?.searchInfo.query}</code>
            </div>

            <div class="consent-actions">
              <button 
                class="btn-secondary" 
                onClick={handleConsentCancel}
              >
                Cancel
              </button>
              <button 
                class="btn-primary" 
                onClick={handleConsentProceed}
              >
                Proceed with Search
              </button>
            </div>
          </div>
        </Modal>
      </Show>
    </div>
  )
}