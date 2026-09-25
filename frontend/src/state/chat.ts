import { createSignal, createMemo } from 'solid-js'
import { createStore } from 'solid-js/store'
import { postChat, postChatStream, createTurnId } from '../services/api'
import { startStatusPolling, stopStatusPolling, setStreamStage } from '../services/status'
import { getSessionMessages } from '../services/api'
import type {
  ChatMessage,
  ExtractionSummary,
  SearchInfo,
  OgData,
  QueuedMessage,
  SessionMessage,
  AttachedFile,
} from '../../types'

interface ChatState {
  conversationMessages: Map<string, ChatMessage[]>
  conversationTurnIds: Map<string, string>
  conversationTurnActive: Map<string, boolean>
  conversationQueues: Map<string, QueuedMessage[]>
}

const [chatState, setChatState] = createStore<ChatState>({
  conversationMessages: new Map(),
  conversationTurnIds: new Map(),
  conversationTurnActive: new Map(),
  conversationQueues: new Map(),
})

export const [sessionId, setSessionId] = createSignal<string | null>(null)
// Per-conversation turn active state
export const isTurnActive = createMemo(() => {
  const sid = sessionId()
  if (!sid) return false
  return chatState.conversationTurnActive.get(sid) === true
})
// Per-conversation queue
export const queue = createMemo(() => {
  const sid = sessionId()
  if (!sid) return []
  return chatState.conversationQueues.get(sid) || []
})
export const [currentTurnId, setCurrentTurnId] = createSignal<string | null>(null)
export const [isStreaming, setIsStreaming] = createSignal(false)
export const [streamingMessageId, setStreamingMessageId] = createSignal<string | null>(null)

export const messages = createMemo(() => {
  const sid = sessionId()
  if (sid) {
    const conversationMessages = chatState.conversationMessages.get(sid) || []
    const currentQueue = queue()
    if (currentQueue.length === 0) {
      return conversationMessages
    }
    const queuedMessages: ChatMessage[] = currentQueue.map(q => ({
      role: 'user' as const,
      content: q.message,
      id: q.id,
      meta: { isQueued: true }
    }))
    return [...conversationMessages, ...queuedMessages]
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
    setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
      const next = new Map(prev)
      const existing = next.get(sessionIdParam) || []
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
      next.set(sessionIdParam, [...loadedMessages, ...active])
      return next
    })
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
      const next = new Map(prev)
      next.set(sessionIdParam, [])
      return next
    })
  }
}

export async function postChatMessage(
  message: string,
  session_id?: string,
  attached_files?: AttachedFile[],
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
  // Set turn active for this specific conversation
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

  startStatusPolling(turnId)

  try {
    const result = await postChat(message, session_id, attached_files, turnId, undefined, max_intelligence)
    return {
      response: result.response,
      task_type: result.task_type,
      extraction_summary: result.extraction_summary,
      search_extraction_summary: result.search_extraction_summary,
      search_info: result.search_info,
      session_id: result.session_id,
    }
  } catch (error) {
    console.error('Error sending message:', error)
    throw error
  } finally {
    stopStatusPolling(turnId)
    setCurrentTurnId(null)

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

    drainQueue(session_id)
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

  startStatusPolling(turnId)

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
    stopStatusPolling(turnId)
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

    drainQueue(session_id)
  }
}

function setStreamingMessageContent(
  sessionIdParam: string,
  messageId: string,
  content: string,
  streamingState?: boolean
): void {
  setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
    const next = new Map(prev)
    const updated = (next.get(sessionIdParam) || []).map(msg =>
      msg.id === messageId
        ? streamingState === undefined
          ? { ...msg, content }
          : { ...msg, content, meta: { ...msg.meta, isStreaming: streamingState } }
        : msg
    )
    next.set(sessionIdParam, updated)
    return next
  })
}

function mergeStreamingMessageMeta(
  sessionIdParam: string,
  messageId: string,
  meta: Partial<NonNullable<ChatMessage['meta']>>
): void {
  setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
    const next = new Map(prev)
    const updated = (next.get(sessionIdParam) || []).map(msg =>
      msg.id === messageId ? { ...msg, meta: { ...msg.meta, ...meta } } : msg
    )
    next.set(sessionIdParam, updated)
    return next
  })
}

export function addMessageToConversation(sessionIdParam: string, message: ChatMessage): void {
  setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
    const next = new Map(prev)
    const existing = next.get(sessionIdParam) || []
    next.set(sessionIdParam, [...existing, message])
    return next
  })
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
  
  const queuedMessage: QueuedMessage = {
    id: `queued-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    message,
    timestamp: Date.now()
  }
  setChatState('conversationQueues', (prev: Map<string, QueuedMessage[]>) => {
    const next = new Map(prev)
    const existing = next.get(targetSessionId) || []
    next.set(targetSessionId, [...existing, queuedMessage])
    return next
  })
}

export function removeQueuedMessage(id: string, sessionIdParam?: string): void {
  const targetSessionId = sessionIdParam || sessionId()
  if (!targetSessionId) return
  
  setChatState('conversationQueues', (prev: Map<string, QueuedMessage[]>) => {
    const next = new Map(prev)
    const existing = next.get(targetSessionId) || []
    next.set(targetSessionId, existing.filter(q => q.id !== id))
    return next
  })
}

export function drainQueue(sessionIdParam?: string): void {
  const targetSessionId = sessionIdParam || sessionId()
  if (!targetSessionId) return
  
  setChatState('conversationQueues', (prev: Map<string, QueuedMessage[]>) => {
    const next = new Map(prev)
    const currentQueue = next.get(targetSessionId) || []
    const [nextMsg, ...rest] = currentQueue
    if (nextMsg) {
      next.set(targetSessionId, rest)
      // Route queued messages through the streaming path so the assistant
      // reply lands in the conversation store and renders (the non-streaming
      // postChatMessage never adds the response to the UI).
      postChatMessageStream(nextMsg.message, targetSessionId)
    }
    return next
  })
}

interface SessionMessage {
  role: string
  content: string
  task_type?: string
  memory_context?: string
  citations?: string[]
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
  ogData?: Record<string, OgData>
}