/**
 * Typed fetch wrapper for API endpoints.
 * Uses Zod schemas for request/response validation.
 */
import { z } from 'zod'
import { debug } from './logger'
import type {
  AttachedFile,
  ChatResponse,
  ExtractionSummary,
  Slot,
  SearchInfo,
  Frame,
  Association,
  FileEntry,
  FileUploadResult,
  User,
  SessionSummary,
  SessionMessage,
  DeletedSession,
  OgPreviewResponse,
} from '../types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

// Zod schemas for validated endpoints only: a schema with no `validate()`
// caller is a false promise that the response was checked, so dead ones are
// removed rather than left lying around.
export const FrameSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  type: z.string(),
  confidence: z.number(),
  essential: z.number(),
  priority: z.number(),
  owner_user_id: z.number().nullable(),
  source_type: z.string().nullable(),
  source_url: z.string().nullable(),
  source_reliability: z.number().nullable(),
  embedding_model: z.string().nullable(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
})

export const AssociationSchema = z.object({
  id: z.number().int(),
  from_frame_id: z.number().int(),
  to_frame_id: z.number().int(),
  relation_type: z.string(),
  confidence: z.number(),
  essential: z.number(),
  priority: z.number(),
  source_type: z.string().nullable(),
  source_url: z.string().nullable(),
  source_reliability: z.number().nullable(),
  embedding_model: z.string().nullable(),
  created_at: z.string().nullable(),
})

export const UserSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  created_at: z.string().nullable(),
})

export const SessionSummarySchema = z.object({
  id: z.string(),
  episode_count: z.number().int(),
  last_activity: z.string().nullable(),
  created_at: z.string().nullable(),
  last_message: z.string(),
})

export const SessionMessageSchema = z.object({
  role: z.string(),
  content: z.string(),
  timestamp: z.string().optional(),
})

export const AssistantNameSchema = z.object({
  name: z.string(),
})

export const SettingsSchema = z.object({
  brave_enabled: z.boolean(),
  brave_configured: z.boolean(),
})

// Re-export types from shared types
export type {
  AttachedFile,
  ChatResponse,
  ExtractionSummary,
  Slot,
  SearchInfo,
  Frame,
  Association,
  FileEntry,
  FileUploadResult,
  User,
  SessionSummary,
  SessionMessage,
  DeletedSession,
  OgPreviewResponse,
}

// API functions with types
export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${BASE_URL}${path}`
  debug('[api] Request:', options.method || 'GET', url)
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    credentials: 'include',
  })

  debug('[api] Response:', response.status, response.statusText)
  // Read the body as text and parse defensively: a proxy or error page can
  // return a non-JSON body (HTML, empty 200, gzip'd error), and response.json()
  // would throw SyntaxError ("unexpected character at line 1 column 1") that
  // surfaces as unhelpful "Failed to fetch alerts" style errors. A disturbed
  // Response stream can't be re-read, so text() first is the only safe order.
  const bodyText = await response.text()
  let data: unknown = null
  if (bodyText.trim()) {
    try {
      data = JSON.parse(bodyText)
    } catch {
      data = null
    }
  }

  if (!response.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : 'Unknown error'
    console.error('[api] Error:', detail)
    throw new Error(detail)
  }

  debug('[api] Response data:', data)
  return data as T
}

/**
 * Parse a response against its Zod schema.
 *
 * The schemas above were dead code: `api()` cast the body with `as Promise<T>`
 * and never parsed it, so a backend field rename or a shape change reached the
 * UI as `undefined` instead of a loud error. This is the contract check the
 * schemas were written for.
 */
function validate<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data)
  if (!result.success) {
    console.error('[api] Schema validation failed:', result.error.issues)
    throw new Error(`Unexpected API response shape: ${result.error.issues[0]?.message ?? 'invalid'}`)
  }
  return result.data
}

export interface StreamEvent {
  type: 'text_delta' | 'tool_call' | 'tool_result' | 'finalize' | 'error' | 'stage' | 'meta'
  delta?: string
  tool_calls?: Array<{ name: string; arguments: Record<string, unknown> }>
  tool_name?: string
  success?: boolean
  data?: Record<string, unknown>
  error?: string
  answer?: string
  reasoning_trace?: string | null
  stage?: string
  detail?: string
  session_id?: string
  task_type?: string
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
}

export async function postChatStream(
  message: string,
  session_id?: string,
  attached_files?: AttachedFile[],
  turn_id?: string,
  search_consent?: boolean,
  max_intelligence?: boolean,
  onEvent?: (event: StreamEvent) => void,
  user_id: number = 1
): Promise<ChatResponse> {
  const requestBody = {
    user_id,
    message,
    session_id,
    attached_files,
    turn_id,
    search_consent,
    max_intelligence,
  }

  const url = `${BASE_URL}/chat/stream`
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include',
    body: JSON.stringify(requestBody),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    console.error('[api] Stream error:', error)
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  if (!response.body) {
    throw new Error('No response body')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  let finalAnswer = ''
  // Accumulators for the ChatResponse-shaped return value. These were
  // previously referenced without being declared, which threw a ReferenceError
  // the moment the stream completed (and the caller then overwrote the good
  // streamed text with 'Error: Failed to send message').
  let finalSessionId: string | undefined
  let finalTaskType: string | undefined
  let finalExtractionSummary: ExtractionSummary | undefined
  let finalSearchExtractionSummary: ExtractionSummary | undefined
  let finalSearchInfo: SearchInfo | undefined

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Split by double newline for SSE events
      const events = buffer.split('\n\n')
      buffer = events.pop() || ''

      for (const eventText of events) {
        if (!eventText.trim()) continue
        if (!eventText.startsWith('data: ')) continue

        const data = eventText.slice(6).trim()
        if (!data || data === '[DONE]') continue

        try {
          const event: StreamEvent = JSON.parse(data)

          if (onEvent) {
            onEvent(event)
          }

          // Accumulate final answer for return value
          if (event.type === 'text_delta' && event.delta) {
            finalAnswer += event.delta
          } else if (event.type === 'finalize') {
            finalAnswer = event.answer || finalAnswer
            // The finalize event doesn't include task_type, extraction_summary, etc.
            // Those would need to be sent in a separate event or we need to handle differently
          } else if (event.type === 'meta') {
            if (event.session_id) finalSessionId = event.session_id
            if (event.task_type) finalTaskType = event.task_type
            if (event.extraction_summary) finalExtractionSummary = event.extraction_summary
            if (event.search_extraction_summary) finalSearchExtractionSummary = event.search_extraction_summary
            if (event.search_info) finalSearchInfo = event.search_info
          }
        } catch (e) {
          console.warn('Failed to parse SSE event:', e, data)
        }
      }
    }

    // Process any remaining buffer
    if (buffer.trim() && buffer.startsWith('data: ')) {
      const data = buffer.slice(6).trim()
      try {
        const event: StreamEvent = JSON.parse(data)
        if (onEvent) onEvent(event)
        if (event.type === 'finalize' && event.answer) {
          finalAnswer = event.answer
        } else if (event.type === 'meta') {
          if (event.session_id) finalSessionId = event.session_id
          if (event.task_type) finalTaskType = event.task_type
          if (event.extraction_summary) finalExtractionSummary = event.extraction_summary
          if (event.search_extraction_summary) finalSearchExtractionSummary = event.search_extraction_summary
          if (event.search_info) finalSearchInfo = event.search_info
        }
      } catch (e) {
        console.warn('Failed to parse final SSE event:', e)
      }
    }
  } finally {
    reader.releaseLock()
  }

  // Return a ChatResponse-like object (note: missing some fields that only come from non-streaming)
  return {
    session_id: finalSessionId || session_id || '',
    response: finalAnswer,
    task_type: finalTaskType,
    extraction_summary: finalExtractionSummary,
    search_extraction_summary: finalSearchExtractionSummary,
    search_info: finalSearchInfo,
  } as ChatResponse
}

export function createTurnId(): string {
  return crypto.randomUUID()
}

export async function getFrames(user_id: number): Promise<Frame[]> {
  debug('Fetching frames for user:', user_id)
  const data = await api<unknown>(`/memory/frames?user_id=${user_id}`)
  return validate(z.array(FrameSchema), data)
}

export async function getAssociations(frame_id: number): Promise<Association[]> {
  debug('Fetching associations for frame:', frame_id)
  const data = await api<unknown>(`/memory/frames/${frame_id}/associations`)
  return validate(z.array(AssociationSchema), data)
}

export async function listFiles(): Promise<FileEntry[]> {
  debug('Listing files')
  return api<FileEntry[]>(`/files/list`)
}

export async function postFileUpload(file: File): Promise<FileUploadResult> {
  debug('Uploading file:', file.name)
  const formData = new FormData()
  formData.append('file', file)
  
  const response = await fetch(`${BASE_URL}/files/upload`, {
    method: 'POST',
    body: formData,
    credentials: 'include',
  })
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }
  
  return response.json()
}

export async function deleteFile(file_id: string): Promise<{ success: boolean }> {
  debug('Deleting file:', file_id)
  const response = await fetch(`${BASE_URL}/files/${file_id}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }
  
  return response.json()
}

export async function getUserSessions(user_id: number): Promise<SessionSummary[]> {
  debug('Fetching sessions for user:', user_id)
  const data = await api<unknown>(`/users/${user_id}/sessions`)
  return validate(z.array(SessionSummarySchema), data)
}

export async function getUsers(): Promise<User[]> {
  const data = await api<unknown>('/users')
  return validate(z.array(UserSchema), data)
}

export async function createNewConversation(user_id: number): Promise<{ session_id: string; message: string }> {
  debug('Creating new conversation for user:', user_id)
  return api<{ session_id: string; message: string }>(`/conversations/new?user_id=${user_id}`, {
    method: 'POST',
  })
}

export async function updateConversationTitle(
  session_id: string,
  user_id: number,
  title: string
): Promise<{ session_id: string; title: string }> {
  debug('Updating conversation title:', { session_id, user_id, title })
  return api<{ session_id: string; title: string }>(
    `/conversations/${encodeURIComponent(session_id)}/title`,
    {
      method: 'PATCH',
      body: JSON.stringify({ user_id, title }),
    }
  )
}

export async function getSessionMessages(
  session_id: string,
  user_id: number,
  limit: number = 50
): Promise<SessionMessage[]> {
  debug('Fetching session messages:', { session_id, user_id, limit })
  const data = await api<unknown>(
    `/chat/session/${encodeURIComponent(session_id)}/messages?user_id=${user_id}&limit=${limit}`
  )
  return validate(z.array(SessionMessageSchema), data)
}

export async function getAssistantName(): Promise<{ name: string }> {
  const data = await api<unknown>('/assistant/name')
  return validate(AssistantNameSchema, data)
}

export async function getSettings(): Promise<{ brave_enabled: boolean; brave_configured: boolean }> {
  const data = await api<unknown>('/settings')
  return validate(SettingsSchema, data)
}

/**
 * The API validates `kind` against this exact enum server-side; typing it here
 * keeps a typo a compile error instead of a runtime 422.
 */
export type FeedbackKind = 'positive' | 'negative' | 'correction'

export async function postFeedback(
  episode_id: string | null,
  message_id: string,
  kind: FeedbackKind,
  comment: string | null
): Promise<{ status: string }> {
  return api<{ status: string }>('/feedback', {
    method: 'POST',
    body: JSON.stringify({ episode_id, message_id, kind, comment }),
  })
}

export async function postCorrection(
  episode_id: string | null,
  message_id: string,
  correction_text: string
): Promise<{ status: string }> {
  return api<{ status: string }>('/correction', {
    method: 'POST',
    body: JSON.stringify({ episode_id, message_id, correction_text }),
  })
}

export async function transcribeAudio(audioBlob: Blob): Promise<{ text: string }> {
  const formData = new FormData()
  formData.append('file', audioBlob, 'audio.webm')
  
  const response = await fetch(`${BASE_URL}/transcribe`, {
    method: 'POST',
    body: formData,
    credentials: 'include',
  })
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }
  
  return response.json()
}

// Conversation trash can
export async function getDeletedSessions(user_id: number): Promise<DeletedSession[]> {
  debug('Fetching deleted sessions for user:', user_id)
  return api<DeletedSession[]>(`/conversations/trash?user_id=${user_id}`)
}

export async function restoreConversation(session_id: string, user_id: number): Promise<{ status: string; session_id: string }> {
  debug('Restoring conversation:', session_id)
  return api<{ status: string; session_id: string }>(
    `/conversations/${encodeURIComponent(session_id)}/restore?user_id=${user_id}`,
    { method: 'POST' }
  )
}

export async function deleteConversation(session_id: string, user_id: number): Promise<{ status: string; session_id: string }> {
  debug('[deleteConversation] Called with:', session_id, user_id)
  return api<{ status: string; session_id: string }>(
    `/conversations/${encodeURIComponent(session_id)}?user_id=${user_id}`,
    { method: 'DELETE' }
  )
}

export async function getOGPreview(url: string): Promise<OgPreviewResponse> {
  return api<OgPreviewResponse>(`/og-preview?url=${encodeURIComponent(url)}`)
}

export interface Alert {
  id: number
  user_id: number
  type: string
  title: string
  message: string
  source_episode_id: number | null
  severity: string
  created_at: string | null
}

export interface AlertsListResponse {
  alerts: Alert[]
  unread_count: number
}

/** One conversation an alert can be resolved in, for the selector. */
export interface AlertConversationOption {
  session_id: string
  name: string
  last_activity: string | null
  message_count: number
}

export interface AlertConversationOptionsResponse {
  alert_id: number
  conversations: AlertConversationOption[]
}

export interface OpenAlertConversationResponse {
  status: string
  alert_id: number
  session_id: string
  /** null when the alert was attached to a conversation with existing history. */
  episode_id: number | null
  seeded: boolean
}

export async function getAlerts(user_id: number, limit: number = 50): Promise<AlertsListResponse> {
  debug('Fetching alerts for user:', user_id)
  return api<AlertsListResponse>(`/alerts?user_id=${user_id}&limit=${limit}`)
}

export async function markAlertAsRead(alert_id: number, user_id: number): Promise<{ status: string }> {
  debug('Marking alert as read:', alert_id)
  return api<{ status: string }>(
    `/alerts/${encodeURIComponent(String(alert_id))}/read?user_id=${user_id}`,
    { method: 'POST' }
  )
}

/** Conversations an alert could be resolved in. Always includes the option list —
 * the backend excludes other alerts' own threads, because resolving one alert
 * inside another's thread entangles two questions. */
export async function getAlertConversationOptions(
  alert_id: number,
  user_id: number,
  limit: number = 20
): Promise<AlertConversationOptionsResponse> {
  debug('Fetching conversation options for alert:', alert_id)
  return api<AlertConversationOptionsResponse>(
    `/alerts/${encodeURIComponent(String(alert_id))}/conversations?user_id=${user_id}&limit=${limit}`
  )
}

/** Attach an alert to a conversation so it can be resolved there.
 *
 * `session_id` is the selector's answer. Omit it and the alert falls back to its
 * own thread — which exists so the call is idempotent, not as the intended path.
 */
export async function openAlertConversation(
  alert_id: number,
  user_id: number,
  session_id?: string
): Promise<OpenAlertConversationResponse> {
  debug('Opening conversation for alert:', alert_id, session_id)
  const query = new URLSearchParams({ user_id: String(user_id) })
  if (session_id) query.set('session_id', session_id)
  return api<OpenAlertConversationResponse>(
    `/alerts/${encodeURIComponent(String(alert_id))}/open?${query.toString()}`,
    { method: 'POST' }
  )
}

export async function markAllAlertsAsRead(user_id: number): Promise<{ status: string }> {
  debug('Marking all alerts as read for user:', user_id)
  return api<{ status: string }>(`/alerts/read-all?user_id=${user_id}`, { method: 'POST' })
}