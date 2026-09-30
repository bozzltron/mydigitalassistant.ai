import { createSignal, createMemo } from 'solid-js'
import { createStore } from 'solid-js/store'
import { postChatStream, createTurnId } from '../services/api'
import { beginTurnStatus, endTurnStatus, setStreamStage } from '../services/status'
import { getSessionMessages } from '../services/api'
import {
  enqueue as mqEnqueue,
  dequeue as mqDequeue,
  drainQueue as mqDrainQueue,
  clearQueue as mqClearQueue,
  setActiveConversation
} from './messageQueue'
import type {
  ChatMessage,
  ExtractionSummary,
  SearchInfo,
  OgData,
  SessionMessage,
  AttachedFile,
} from '../types'

interface ChatState {
  conversationMessages: Record<string, ChatMessage[]>
  conversationTurnIds: Map<string, string>
  conversationTurnActive: Map<string, boolean>
  // conversationQueues removed - now using messageQueue
}

const [chatState, setChatState] = createStore<ChatState>({
  conversationMessages: {},
  conversationTurnIds: new Map(),
  conversationTurnActive: new Map(),
})

export const [sessionId, setSessionId] = createSignal<string | null>(null)
// Per-conversation turn active state
export const isTurnActive = createMemo(() => {
  const sid = sessionId()
  if (!sid) return false
  return chatState.conversationTurnActive.get(sid) === true
})
// The message queue is owned by state/messageQueue. It used to be re-exported
// here as `queue` / `queueLength` memos, but the transcript no longer reads it
// (pending items render in the ChatPage queue panel instead), so these were
// duplicate accessors with no callers. Use getQueue()/getQueueLength() directly.
export const [currentTurnId, setCurrentTurnId] = createSignal<string | null>(null)
export const [isStreaming, setIsStreaming] = createSignal(false)
export const [streamingMessageId, setStreamingMessageId] = createSignal<string | null>(null)

export const messages = createMemo(() => {
  const sid = sessionId()
  if (sid) {
    // Queued messages are deliberately NOT appended to the transcript. The
    // queue panel in ChatPage renders them, with the pending styling, the
    // source icon and a remove control. Appending them here made every queued
    // message render twice: once in the transcript and once in the panel.
    // MessageList ignores meta.isQueued, so the transcript copy looked like an
    // already-sent message -- it read as "sent and queued" at the same time.
    return chatState.conversationMessages[sid] || []
  }
  return []
})

export function initChat(): void {
  if (typeof localStorage !== 'undefined') {
    const saved = localStorage.getItem('session_id')
    if (saved) setSessionId(saved)
  }
}

export async function loadConversationMessages(sessionIdParam: string, userId: number): Promise<void> {
  try {
    const data = await getSessionMessages(sessionIdParam, userId, 50)
    const loadedMessages: ChatMessage[] = data.map((m: SessionMessage, index: number) => {
      const mm = m as SessionMessage & {
        task_type?: string
        memory_context?: string
        citations?: string[]
        extraction_summary?: ExtractionSummary
        search_extraction_summary?: ExtractionSummary
        search_info?: SearchInfo
        ogData?: Record<string, OgData>
      }
      return {
        role: m.role as 'user' | 'assistant',
        content: m.content,
        id: `history-${sessionIdParam}-${index}`,
        meta: {
          task_type: mm.task_type,
          memory_context: mm.memory_context,
          citations: mm.citations,
          extraction_summary: mm.extraction_summary,
          search_extraction_summary: mm.search_extraction_summary,
          search_info: mm.search_info,
          ogData: mm.ogData,
        },
      }
    })
    setChatState('conversationMessages', (prev) => {
      const existing = prev[sessionIdParam] || []
      // Preserve optimistic messages that the server hasn't persisted yet:
      // a history load racing an in-flight turn would otherwise wipe the user
      // bubble and the streaming placeholder, orphaning the live stream (the
      // response then only appears after a refresh). Skip history rows and
      // anything the server already saved to avoid duplicates.
      const active = existing.filter(m => {
        if (m.id.startsWith('history-')) return false
        if (m.meta?.isStreaming === true) return true
        if (m.role !== 'user') return false
        const persisted = loadedMessages.some(
          lm => lm.role === 'user' && lm.content === m.content && m.content !== ''
        )
        return !persisted
      })
      return { [sessionIdParam]: [...loadedMessages, ...active] }
    })
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    setChatState('conversationMessages', sessionIdParam, [])
  }
}

export async function postChatMessageStream(
  message: string,
  session_id?: string,
  attached_files?: AttachedFile[],
  search_consent?: boolean,
  max_intelligence?: boolean
): Promise<{
  response: string
  task_type?: string
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
  session_id?: string
}> {
  const turnId = createTurnId()
  setCurrentTurnId(turnId)
  setIsStreaming(true)

  if (session_id) {
    setChatState('conversationTurnActive', (prev: Map<string, boolean>) => {
      const next = new Map(prev)
      next.set(session_id, true)
      return next
    })
    setChatState('conversationTurnIds', (prev: Map<string, string>) => {
      const next = new Map(prev)
      next.set(session_id, turnId)
      return next
    })
  }

  beginTurnStatus(turnId)

  // Create a placeholder assistant message that will be updated during streaming
  const assistantMessageId = `streaming-${turnId}-assistant`
  setStreamingMessageId(assistantMessageId)

  if (session_id) {
    const placeholderMessage: ChatMessage = {
      role: 'assistant',
      content: '',
      id: assistantMessageId,
      meta: { isStreaming: true }
    }
    addMessageToConversation(session_id, placeholderMessage)
  }

  let accumulatedResponse = ''

  try {
    const result = await postChatStream(
      message,
      session_id,
      attached_files,
      turnId,
      search_consent,
      max_intelligence,
      (event) => {
        if (event.type === 'text_delta' && event.delta) {
          accumulatedResponse += event.delta
          // Update the streaming message in real-time
          if (session_id) {
            setStreamingMessageContent(session_id, assistantMessageId, accumulatedResponse)
          }
        } else if (event.type === 'stage' && event.stage) {
          // Live pipeline progress rides the SSE stream itself.
          setStreamStage(turnId, event.stage, event.detail)
        } else if (event.type === 'meta') {
          // Final stream metadata (task type, extraction/search summaries, search
          // info) lands on the streaming bubble so search + learning transparency
          // renders (Brave indicator, "what I learned", trace panel, media).
          if (session_id && (event.task_type || event.extraction_summary ||
              event.search_extraction_summary || event.search_info)) {
            mergeStreamingMessageMeta(session_id, assistantMessageId, {
              ...(event.task_type ? { task_type: event.task_type } : {}),
              ...(event.extraction_summary ? { extraction_summary: event.extraction_summary } : {}),
              ...(event.search_extraction_summary
                ? { search_extraction_summary: event.search_extraction_summary }
                : {}),
              ...(event.search_info ? { search_info: event.search_info } : {}),
            })
          }
        } else if (event.type === 'tool_result') {
          // Tool completed - check if it's a file operation that should refresh the file list
          const fileOps = ['write_file', 'edit_file', 'delete_file', 'glob', 'list_files']
          if (event.tool_name && fileOps.includes(event.tool_name)) {
            // Dispatch a custom event that FileGrid can listen for
            if (typeof window !== 'undefined') {
              window.dispatchEvent(new CustomEvent('files-changed', { detail: { tool: event.tool_name } }))
            }
          }
        } else if (event.type === 'finalize') {
          accumulatedResponse = event.answer || accumulatedResponse
          // Final update
          if (session_id) {
            setStreamingMessageContent(session_id, assistantMessageId, accumulatedResponse, false)
          }
        } else if (event.type === 'error') {
          console.error('Stream error:', event.error)
          if (session_id) {
            setStreamingMessageContent(session_id, assistantMessageId, 'Error: ' + event.error, false)
          }
        }
      }
    )

    return {
      response: accumulatedResponse,
      task_type: result.task_type,
      extraction_summary: result.extraction_summary,
      search_extraction_summary: result.search_extraction_summary,
      search_info: result.search_info,
      session_id: result.session_id,
    }
  } catch (error) {
    console.error('Error sending streaming message:', error)
    if (session_id) {
      setStreamingMessageContent(session_id, assistantMessageId, 'Error: Failed to send message', false)
    }
    throw error
  } finally {
    endTurnStatus(turnId)
    setCurrentTurnId(null)
    setIsStreaming(false)
    setStreamingMessageId(null)

    if (session_id) {
      setChatState('conversationTurnActive', (prev: Map<string, boolean>) => {
        const next = new Map(prev)
        next.delete(session_id)
        return next
      })
      setChatState('conversationTurnIds', (prev: Map<string, string>) => {
        const next = new Map(prev)
        next.delete(session_id)
        return next
      })
    }

    // Keep the active conversation current so the queue drainer knows where to
    // send. Do NOT drain here: draining clears the queue, which silently
    // destroyed messages the user queued while this turn was streaming.
    if (session_id) {
      setActiveConversation(session_id)
    }
  }
}

function setStreamingMessageContent(
  sessionIdParam: string,
  messageId: string,
  content: string,
  streamingState?: boolean
): void {
  // Fine-grained: walk to the streaming message and update only its fields.
  // Rebuilding the array (and the old Map) on every text_delta invalidated the
  // whole transcript memo, so the stream cost O(messages) per token.
  const list = chatState.conversationMessages[sessionIdParam]
  if (!list) return
  const index = list.findIndex(msg => msg.id === messageId)
  if (index === -1) return

  setChatState('conversationMessages', sessionIdParam, index, 'content', content)
  if (streamingState !== undefined) {
    setChatState('conversationMessages', sessionIdParam, index, 'meta', {
      ...list[index].meta,
      isStreaming: streamingState,
    })
  }
}

function mergeStreamingMessageMeta(
  sessionIdParam: string,
  messageId: string,
  meta: Partial<NonNullable<ChatMessage['meta']>>
): void {
  const list = chatState.conversationMessages[sessionIdParam]
  if (!list) return
  const index = list.findIndex(msg => msg.id === messageId)
  if (index === -1) return

  setChatState('conversationMessages', sessionIdParam, index, 'meta', {
    ...list[index].meta,
    ...meta,
  })
}

export function addMessageToConversation(sessionIdParam: string, message: ChatMessage): void {
  const existing = chatState.conversationMessages[sessionIdParam] || []
  // Re-committing an id happens when a send is retried, and the transcript
  // must not grow a second copy of the same turn.
  if (existing.some((m) => m.id === message.id)) return
  setChatState('conversationMessages', sessionIdParam, [...existing, message])
}

export function getConversationTurnId(sessionIdParam: string): string | undefined {
  return chatState.conversationTurnIds.get(sessionIdParam)
}

export function useConversationTurnId(sessionIdParam: () => string | null | undefined) {
  return createMemo(() => {
    const sid = sessionIdParam()
    if (!sid) return undefined
    return chatState.conversationTurnIds.get(sid)
  })
}

export function enqueueMessage(message: string, sessionIdParam?: string): void {
  const targetSessionId = sessionIdParam || sessionId()
  if (!targetSessionId) return

  mqEnqueue({
    content: message,
    source: 'text',
    timestamp: Date.now(),
  })
}

export function removeQueuedMessage(id: string, _sessionIdParam?: string): void {
  // _sessionIdParam is ignored - messageQueue is single-conversation
  mqDequeue(id)
}

export function drainQueue(sessionIdParam?: string): void {
  const targetSessionId = sessionIdParam || sessionId()
  if (!targetSessionId) return

  setActiveConversation(targetSessionId)
  mqDrainQueue()
}

export function clearQueue(): void {
  mqClearQueue()
}