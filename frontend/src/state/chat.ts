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
}

const [chatState, setChatState] = createStore<ChatState>({
  conversationMessages: new Map(),
  conversationTurnIds: new Map(),
})

export const [sessionId, setSessionId] = createSignal<string | null>(null)
export const [queue, setQueue] = createSignal<QueuedMessage[]>([])
export const [isTurnActive, setTurnActive] = createSignal(false)
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
      next.set(sessionIdParam, loadedMessages)
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
  setTurnActive(true)

  if (session_id) {
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
    setTurnActive(false)

    if (session_id) {
      setChatState('conversationTurnIds', (prev: Map<string, string>) => {
        const next = new Map(prev)
        next.delete(session_id)
        return next
      })
    }

    drainQueue()
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
  setTurnActive(true)
  setIsStreaming(true)

  if (session_id) {
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
    setTurnActive(false)
    setIsStreaming(false)
    setStreamingMessageId(null)

    if (session_id) {
      setChatState('conversationTurnIds', (prev: Map<string, string>) => {
        const next = new Map(prev)
        next.delete(session_id)
        return next
      })
    }

    drainQueue()
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

export function enqueueMessage(message: string): void {
  const queuedMessage: QueuedMessage = {
    id: `queued-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    message,
    timestamp: Date.now()
  }
  setQueue(prev => [...prev, queuedMessage])
}

export function removeQueuedMessage(id: string): void {
  setQueue(prev => prev.filter(q => q.id !== id))
}

export function drainQueue(): void {
  setQueue(prev => {
    const [next, ...rest] = prev
    if (next) {
      const currentSessionId = sessionId()
      if (currentSessionId) {
        postChatMessage(next.message, currentSessionId)
      }
    }
    return rest
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