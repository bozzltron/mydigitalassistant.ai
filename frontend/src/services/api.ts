/**
 * Typed fetch wrapper for API endpoints.
 * Uses Zod schemas for request/response validation.
 */
import { z } from 'zod'
import type {
  AttachedFile,
  ChatResponse,
  ExtractionSummary,
  Slot,
  SearchInfo,
  Frame,
  Association,
  SearchResult,
  FileEntry,
  User,
  Session,
  SessionMessage,
  OgPreviewResponse,
} from '../../types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

// Define Zod schemas for API responses (simplified versions)
export const ChatRequestSchema = z.object({
  user_id: z.number().int(),
  message: z.string(),
  session_id: z.string().uuid().optional(),
  turn_id: z.string().uuid().optional(),
  attached_files: z.array(z.object({
    name: z.string(),
    ext: z.string(),
    preview: z.string(),
    content: z.string(),
    text: z.string(),
    key_entities: z.array(z.string()),
    open_questions: z.array(z.string()),
  })).optional(),
})

export const ExtractionSummarySchema = z.object({
  slots_applied: z.number(),
  associations_created: z.number(),
  conflicts_created: z.number(),
  frame_ids: z.array(z.number()),
  slots: z.array(z.object({
    frame_name: z.string(),
    key: z.string(),
    value: z.string(),
    conflict: z.boolean().optional(),
  })),
})

export const SearchInfoSchema = z.object({
  backend: z.string(),
  query: z.string(),
  engines: z.array(z.string()),
})

export const ChatResponseSchema = z.object({
  session_id: z.string().uuid(),
  response: z.string(),
  task_type: z.enum(['functional', 'introspective', 'search', 'scheduled', 'correction']),
  memory_context: z.string().optional(),
  citations: z.array(z.string()).optional(),
  extraction_summary: ExtractionSummarySchema.optional(),
  search_extraction_summary: ExtractionSummarySchema.optional(),
  search_info: SearchInfoSchema.optional(),
})

export const FrameSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  type: z.string(),
  confidence: z.number(),
  essential: z.boolean(),
  priority: z.number(),
  owner_user_id: z.number().nullable(),
  source_type: z.string().nullable(),
  source_url: z.string().nullable(),
  source_reliability: z.number().nullable(),
  embedding_model: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
})

export const AssociationSchema = z.object({
  id: z.number().int(),
  from_frame_id: z.number().int(),
  to_frame_id: z.number().int(),
  relation_type: z.string(),
  confidence: z.number(),
  essential: z.boolean(),
  priority: z.number(),
  source_type: z.string().nullable(),
  source_url: z.string().nullable(),
  source_reliability: z.number().nullable(),
  embedding_model: z.string().nullable(),
  created_at: z.string(),
})

export const SearchResultSchema = z.object({
  id: z.number().int(),
  query: z.string(),
  results: z.array(z.object({
    url: z.string(),
    title: z.string(),
    content: z.string(),
    relevance: z.number(),
  })),
})

export const FileEntrySchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  size: z.number(),
  type: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
})

export const UserSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  created_at: z.string(),
})

export const SessionSchema = z.object({
  id: z.string(),
  user_id: z.number().int(),
  title: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  episode_count: z.number().int(),
  last_message: z.string().nullable(),
})

export const SessionMessageSchema = z.object({
  role: z.string(),
  content: z.string(),
  timestamp: z.string().optional(),
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
  SearchResult,
  FileEntry,
  User,
  Session,
  SessionMessage,
  OgPreviewResponse,
}

// API functions with types
export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${BASE_URL}${path}`
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  return response.json() as Promise<T>
}

export async function postChat(
  message: string,
  session_id?: string,
  attached_files?: AttachedFile[],
  turn_id?: string
): Promise<ChatResponse> {
  const requestBody = {
    user_id: 1,
    message,
    session_id,
    attached_files,
    turn_id,
  }

  console.log('Sending chat request:', requestBody)

  return api<ChatResponse>('/chat', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function createTurnId(): string {
  return crypto.randomUUID()
}

export async function getFrames(user_id: number): Promise<Frame[]> {
  console.log('Fetching frames for user:', user_id)
  return api<Frame[]>(`/memory/frames?user_id=${user_id}`)
}

export async function getAssociations(frame_id: number): Promise<Association[]> {
  console.log('Fetching associations for frame:', frame_id)
  return api<Association[]>(`/memory/frames/${frame_id}/associations`)
}

export async function getSearchResults(query: string, minRelevance?: number): Promise<SearchResult> {
  console.log('Searching:', { query, minRelevance })
  const params = new URLSearchParams({ q: query })
  if (minRelevance !== undefined) {
    params.append('min_relevance', String(minRelevance))
  }
  return api<SearchResult>(`/search?${params.toString()}`)
}

export async function listFiles(): Promise<FileEntry[]> {
  console.log('Listing files')
  return api<FileEntry[]>(`/files/list`)
}

export async function postFileUpload(file: File): Promise<FileEntry> {
  console.log('Uploading file:', file.name)
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
  console.log('Deleting file:', file_id)
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

export async function getUserSessions(user_id: number): Promise<Session[]> {
  console.log('Fetching sessions for user:', user_id)
  return api<Session[]>(`/users/${user_id}/sessions`)
}

export async function createNewConversation(user_id: number): Promise<{ session_id: string; message: string }> {
  console.log('Creating new conversation for user:', user_id)
  return api<{ session_id: string; message: string }>(`/conversations/new?user_id=${user_id}`, {
    method: 'POST',
  })
}

export async function updateConversationTitle(
  session_id: string,
  user_id: number,
  title: string
): Promise<{ session_id: string; title: string }> {
  console.log('Updating conversation title:', { session_id, user_id, title })
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
  console.log('Fetching session messages:', { session_id, user_id, limit })
  return api<SessionMessage[]>(
    `/chat/session/${encodeURIComponent(session_id)}/messages?user_id=${user_id}&limit=${limit}`
  )
}

export async function getAssistantName(): Promise<{ name: string }> {
  return api<{ name: string }>('/assistant/name')
}

export async function getSettings(): Promise<{ brave_enabled: boolean; brave_configured: boolean }> {
  return api<{ brave_enabled: boolean; brave_configured: boolean }>('/settings')
}

export async function postFeedback(
  episode_id: string | null,
  message_id: string,
  kind: string,
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

export async function getOGPreview(url: string): Promise<OgPreviewResponse> {
  return api<OgPreviewResponse>(`/og-preview?url=${encodeURIComponent(url)}`)
}