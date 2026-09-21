import { createSignal, createEffect, Show, For, onMount, onCleanup } from 'solid-js'
import MessageList from './MessageList'
import InputBar from './InputBar'
import StatusIndicator from './StatusIndicator'
import { Modal } from '../ui/Modal'
import { messages, sessionId, setSessionId, isTurnActive, useConversationTurnId, addMessageToConversation, enqueueMessage, removeQueuedMessage, queue } from '../../state/chat'
import { Session } from '../../state/session'
import { useTurnStatus } from '../../services/status'
import { useVoiceRecording } from '../../hooks/useVoiceRecording'
import { voice, setTtsSpeaking } from '../../state/voice'
import { settings } from '../../state/settings'
import type {
  ExtractionSummary,
  SearchInfo,
  AttachedFile,
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
  sendMessage: (message: string, session_id?: string, attached_files?: AttachedFile[], search_consent?: boolean) => Promise<SendMessageResult>
}) {
  const [showTrace, setShowTrace] = createSignal(false)
  const [messagesContainerRef, setMessagesContainerRef] = createSignal<HTMLDivElement | null>(null)
  const [isDictating, setIsDictating] = createSignal(false)
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

    // If a turn is active, queue the message instead of sending immediately
    if (isSending()) {
      enqueueMessage(message.trim())
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

      const result = await props.sendMessage(message, currentSessionId, processedFiles, search_consent)

      if (result.session_id) {
        setSessionId(result.session_id)
        localStorage.setItem('session_id', result.session_id)
      }

      // Handle search consent required
      if (result.task_type === 'search_consent_required' && result.search_info) {
        setPendingSearchConsent({
          message,
          attachedFiles: processedFiles,
          searchInfo: result.search_info,
        })
        // Show the consent message as assistant response
        const consentMessage = {
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
        addMessageToConversation(currentSessionId, consentMessage)
        return
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

  // TTS: speak assistant responses when they arrive
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
        utterance.onstart = () => setTtsSpeaking(true)
        utterance.onend = () => setTtsSpeaking(false)
        utterance.onerror = () => setTtsSpeaking(false)
        speechSynthesis.speak(utterance)
      }
    }
  })

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
            isDictating={isDictating()}
            onDictationStart={handleDictationStart}
            onDictationStop={handleDictationStop}
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