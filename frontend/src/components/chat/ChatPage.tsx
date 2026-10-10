import { createSignal, createEffect, createMemo, Show, For, onMount, onCleanup } from 'solid-js'
import MessageList from './MessageList'
import InputBar from './InputBar'
import StatusIndicator from './StatusIndicator'
import TracePanel from './TracePanel'
import { Modal } from '../ui/Modal'
import { messages, sessionId, isTurnActive, useConversationTurnId, addMessageToConversation, isStreaming } from '../../state/chat'
import { getQueue, getQueueLength, dequeue, enqueue, isProcessing, setActiveConversation, isDrainBlocked } from '../../state/messageQueue'
import { triggerDrain, drainQueueIfReady, onSearchConsentRequired } from '../../services/queueDrainer'
import { postFileUpload } from '../../services/api'
import type { Session } from '../../state/session'
import { useTurnStatus } from '../../services/status'
import { useVoiceRecording } from '../../hooks/useVoiceRecording'
import { useConversationVoiceRecording } from '../../hooks/useConversationVoiceRecording'
import { speakReplacing, startDictation, endDictation, isVoiceModeActive, voice } from '../../state/voice'
import { settings, updateSetting } from '../../state/settings'
import { refreshFileList } from '../../state/files'
import { initQueue } from '../../state/messageQueue'
import type {
  SearchInfo,
  AttachedFile,
} from '../../types'

// Wrapper component that calls useTurnStatus with a dynamic turnId
function StatusWrapper(props: { turnId: () => string | undefined }) {
  // Pass a thunk that defers the prop read rather than props.turnId itself.
  // useTurnStatus wraps whatever it gets in a createMemo, so handing it the
  // prop reference meant that memo tracked a value snapshotted at setup.
  const { turnStatus, isPolling } = useTurnStatus(() => props.turnId())
  return <StatusIndicator turnStatus={turnStatus} isPolling={isPolling} />
}

export default function ChatPage(props: {
  conversation: Session | null
}) {
  const [messagesContainerRef, setMessagesContainerRef] = createSignal<HTMLDivElement | null>(null)
  const [dictation, setDictation] = createSignal<{ text: string; seq: number }>({ text: '', seq: 0 })
  const [maxIntelligence, setMaxIntelligence] = createSignal(false)
  const [pendingSearchConsent, setPendingSearchConsent] = createSignal<{
    message: string
    attachedFiles: AttachedFile[] | undefined
    searchInfo: SearchInfo
  } | null>(null)

  const isSending = isTurnActive

  // Get the current conversation's turnId
  const currentConvTurnId = useConversationTurnId(sessionId)

  // The trace panel reflects the most recent assistant turn's transparency data.
  // The live meta event carries task_type / memory_context / citations /
  // search_info; a reloaded turn carries only what was persisted (search_info).
  const lastTraceMeta = createMemo(() => {
    const msgs = messages()
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i]
      if (m && m.role === 'assistant' && m.meta) return m.meta
    }
    return undefined
  })

  // Auto-scroll to bottom when messages change
  let scrollTimeout: number | null = null
  createEffect(() => {
    // Subscribe to the transcript only. This used to also read `queue()` so the
    // list would scroll when a message was queued, back when queued items were
    // appended to the transcript. They now render in the queue panel instead, so
    // that subscription only caused spurious scrolls.
    messages()
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
    // The file list powers filename links in messages (see utils/fileLinks).
    // Refresh it here and whenever a tool changes files.
    void refreshFileList()
    const onFilesChanged = () => void refreshFileList()
    window.addEventListener('files-changed', onFilesChanged)
    onCleanup(() => window.removeEventListener('files-changed', onFilesChanged))
  })

  // Read file and convert to AttachedFile format
  /**
   * Upload a file and return a reference to it, or null if the upload failed.
   *
   * Files used to be read with `readAsText` and their content embedded in the
   * chat JSON. That corrupts every binary format before it ever reaches the
   * server — a PDF read as UTF-8 is garbage — so the bytes must travel as bytes.
   * The multipart upload endpoint already does this correctly and is what the
   * files page uses, so the input bar reuses it: upload first, then reference
   * the stored file by frame. One upload path, no content in the chat payload.
   *
   * Returning null rather than throwing is deliberate: an attachment that fails
   * (the backend rejects an unsupported type, or the request drops) must not take
   * the user's typed message with it.
   */
  const uploadAttachedFile = async (file: File): Promise<AttachedFile | null> => {
    const ext = file.name.split('.').pop()?.toLowerCase() || 'txt'
    try {
      const uploaded = await postFileUpload(file)
      return {
        name: file.name,
        ext,
        preview: uploaded.content_preview ?? '',
        // Text is deliberately empty: the bytes live on disk and are read via
        // read_file. Memory records what a file *is*, never what it contains.
        text: '',
        key_entities: uploaded.key_entities ?? [],
        open_questions: uploaded.open_questions ?? [],
        frame_id: uploaded.frame_id,
        frame_name: uploaded.frame_name,
      }
    } catch (e) {
      console.error(`Failed to attach ${file.name}:`, e)
      return null
    }
  }

  const handleSendMessage = async (message: string, attachedFiles?: File[], search_consent?: boolean) => {
    if (!message.trim()) return

    const currentSessionId = sessionId()
    if (!currentSessionId) return

    // Ensure message queue knows the active conversation
    setActiveConversation(currentSessionId)

    // Every entrypoint funnels through the queue. The drain effect below sends
    // it as soon as the agent is ready, so a message submitted mid-turn is
    // stacked rather than dropped or sent out of order.
    //
    // A failed attachment is dropped, not fatal: the message is what the user
    // typed, and losing it because a file was rejected is the worse outcome.
    const processedFiles: AttachedFile[] =
      attachedFiles && attachedFiles.length > 0
        ? (await Promise.all(attachedFiles.map(uploadAttachedFile))).filter(
            (f): f is AttachedFile => f !== null,
          )
        : []

    enqueue({
      content: message.trim(),
      source: 'text',
      timestamp: Date.now(),
      attachedFiles: processedFiles.length > 0 ? processedFiles : undefined,
      searchConsent: search_consent,
      maxIntelligence: maxIntelligence(),
    })

    // Nudge the drainer. It no-ops when the agent is busy or already draining.
    void triggerDrain()
  }

  const handleConsentProceed = async () => {
    const consent = pendingSearchConsent()
    if (!consent) return
    setPendingSearchConsent(null)
    const currentSessionId = sessionId()
    if (!currentSessionId) return
    setActiveConversation(currentSessionId)

    // Retry the original message with consent granted. It is already in history
    // from the first attempt, so skipHistory stops the drainer duplicating it.
    enqueue({
      content: consent.message,
      source: 'text',
      timestamp: Date.now(),
      attachedFiles: consent.attachedFiles,
      searchConsent: true,
      maxIntelligence: maxIntelligence(),
      skipHistory: true,
    })
    void triggerDrain()
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
        setSpokenMsgIds(new Set<string>(JSON.parse(saved) as string[]))
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
        // speakReplacing cancels anything still in flight rather than queueing
        // behind it. speak() queues, so a second answer arriving mid-sentence
        // left both playing and the speaking window growing with every queued
        // message -- while the single boolean gate opened in the gap between one
        // utterance's onend and the next one's onstart.
        speakReplacing(lastMsg.content, (utterance) => {
          const voiceUri = settings.voiceUri
          if (voiceUri) {
            const voices = speechSynthesis.getVoices()
            const selectedVoice = voices.find(v => v.voiceURI === voiceUri)
            if (selectedVoice) utterance.voice = selectedVoice
          }
          utterance.rate = settings.voiceSpeed
          utterance.pitch = settings.voicePitch
          utterance.volume = settings.voiceVolume
        })
      }
    }
  })

  // The single auto-drain trigger. Every entrypoint (input bar, dictation,
  // conversation voice) only enqueues; this effect sends whatever is waiting as
  // soon as the agent is free. Watching the reactive signals means the queue is
  // also drained after a turn started by any other code path. isDrainBlocked
  // stops a message that arrived while the backend was refusing work from being
  // sent in a hot loop.
  createEffect(() => {
    const conversationId = sessionId();

    // Keep the queue's notion of the active conversation in sync with the one
    // on screen. The conversation-voice hook enqueues directly and never called
    // setActiveConversation, so on a fresh session activeConversationId stayed
    // null and the drainer skipped every send with "No active conversation".
    if (conversationId) {
      setActiveConversation(conversationId);
    }

    const waiting = getQueueLength();
    const turnActive = isTurnActive();
    const processing = isProcessing();
    const blocked = isDrainBlocked();

    if (waiting > 0 && !turnActive && !processing && !blocked && conversationId) {
      void drainQueueIfReady();
    }
  });

  // Search consent is raised by the backend during the drainer's send; forward
  // it to the modal that lives in this component.
  onMount(() => {
    const off = onSearchConsentRequired(({ searchInfo, content }) => {
      setPendingSearchConsent({ message: content, attachedFiles: undefined, searchInfo });
    });
    onCleanup(off);
  });

  // Initialize message queue on mount
  onMount(() => {
    initQueue()
  })

  // Dictation mode (one-shot mic button) - uses original hook
  useVoiceRecording({
    isVoiceMode: () => false, // Dictation doesn't use voice mode
    isDictationMode: () => voice.isDictating,
    isTurnActive: () => isTurnActive(),
    // Fill the message box so the user can review and send. Sending straight
    // from dictation (the old behaviour) also started continuous voice mode,
    // because dictation reuses the 'listening' status.
    onTranscription: (text) => setDictation(d => ({ text, seq: d.seq + 1 })),
  })

  // Conversation mode (continuous voice) - uses new queue-based hook
  useConversationVoiceRecording({
    isVoiceMode: () => isVoiceModeActive(),
    isTurnActive: () => isTurnActive(),
  })

  const handleRemoveQueued = (id: string) => {
    dequeue(id)
  }

  const handleDictationStart = () => {
    startDictation()
  }

  const handleDictationStop = () => {
    endDictation()
  }

  return (
    <div id="main">
      <div id="chat-area" class="chat-area">
        <div id="messages" ref={setMessagesContainerRef}>
          {/* Keyed on the session so switching or first-loading a conversation
              remounts the transcript and replays the enter animation. */}
          <Show when={sessionId() ?? '__none__'} keyed>
            {(_sid) => (
              <div class="chat-transcript">
                <MessageList messages={messages} />

                <StatusWrapper turnId={currentConvTurnId} />

                {/* Queue Panel - shows queued messages during processing */}
                <Show when={getQueueLength() > 0}>
                  <div class="queue-panel" id="queue-panel">
                    <div class="queue-panel-content">
                      <For each={getQueue()}>
                        {(queuedMsg) => (
                          <div class="msg msg-user msg-queued" id={queuedMsg.id}>
                            <div class="queued-content">
                              <span class="queued-text">{queuedMsg.content}</span>
                            </div>
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
                    </div>
                  </div>
                </Show>

                <Show when={messages().length === 0 && getQueueLength() === 0}>
                  <div class="welcome-message" id="welcome">
                    <div class="welcome-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
                    <h2>{props.conversation?.title ? `Conversation: ${props.conversation.title}` : 'Ready to chat'}</h2>
                    <p>Type a message below or click Voice to start hands-free.</p>
                  </div>
                </Show>
              </div>
            )}
          </Show>
        </div>

        <div id="input-row">
          <InputBar 
            onSend={handleSendMessage} 
            isSending={isSending()}
            isStreaming={isStreaming()}
            isDictating={voice.isDictating}
            onDictationStart={handleDictationStart}
            onDictationStop={handleDictationStop}
            dictatedText={dictation().text}
            dictatedSeq={dictation().seq}
            maxEnabled={maxIntelligence()}
            onToggleMax={() => setMaxIntelligence((v) => !v)}
          />
        </div>
      </div>

      <TracePanel
        visible={settings.traceVisible}
        meta={lastTraceMeta()}
        onClose={() => updateSetting('traceVisible', false)}
      />

      {/* Search Consent Modal */}
      <Show when={pendingSearchConsent()} keyed>
        {(consent) => {
          const sensitivity = consent.searchInfo.sensitivity
          return (
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

                <Show when={sensitivity}>
                  <div class="consent-details">
                    <div class="consent-level">
                      <span class={`consent-badge ${sensitivity!.level}`}>{sensitivity!.level}</span>
                      <span>{sensitivity!.reason}</span>
                    </div>
                    <Show when={sensitivity!.categories.length > 0}>
                      <div class="consent-categories">
                        <strong>Categories:</strong>
                        <ul>
                          <For each={sensitivity!.categories}>
                            {(cat) => <li>{cat}</li>}
                          </For>
                        </ul>
                      </div>
                    </Show>
                  </div>
                </Show>

                <div class="consent-query">
                  <strong>Query:</strong>
                  <code>{consent.searchInfo.query}</code>
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
          )
        }}
      </Show>
    </div>
  )
}