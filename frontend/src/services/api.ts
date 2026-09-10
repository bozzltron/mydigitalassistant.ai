/**
 * Typed fetch wrapper for API endpoints.
 * Uses Zod schemas for request/response validation.
 */
import { z } from 'zod'

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

export const ChatResponseSchema = z.object({
  session_id: z.string().uuid(),
  response: z.string(),
  task_type: z.enum(['functional', 'introspective', 'search', 'scheduled', 'correction']),
  memory_context: z.string().optional(),
  citations: z.array(z.string()).optional(),
  extraction_summary: z.object({
    slots_applied: z.number(),
    associations_created: z.number(),
    conflicts_created: z.number(),
    frame_ids: z.array(z.number()),
    slots: z.array(z.any()),
  }).optional(),
  search_extraction_summary: z.object({}).optional(),
  search_info: z.object({
    backend: z.string(),
    query: z.string(),
    engines: z.array(z.string()),
  }).optional(),
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
  attached_files?: any[],
  turn_id?: string
) {
  const requestBody = {
    user_id: 1,
    message,
    session_id,
    attached_files,
    turn_id,
  }

  console.log('Sending chat request:', requestBody)

  return api<{
    session_id: string
    response: string
    task_type: string
    memory_context?: string
    citations?: string[]
    extraction_summary?: any
    search_extraction_summary?: any
    search_info?: any
  }>('/chat', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function createTurnId(): string {
  return crypto.randomUUID()
}

export async function getFrames(user_id: number) {
  console.log('Fetching frames for user:', user_id)
  return api<any[]>(`/memory/frames?user_id=${user_id}`)
}

export async function getAssociations(frame_id: number) {
  console.log('Fetching associations for frame:', frame_id)
  return api<any[]>(`/memory/frames/${frame_id}/associations`)
}

export async function getSearchResults(query: string, minRelevance?: number) {
  console.log('Searching:', { query, minRelevance })
  const params = new URLSearchParams({ q: query })
  if (minRelevance !== undefined) {
    params.append('min_relevance', String(minRelevance))
  }
  return api<any>(`/search?${params.toString()}`)
}

export async function listFiles() {
  console.log('Listing files')
  return api<any[]>(`/files/list`)
}

export async function postFileUpload(file: File) {
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

export async function deleteFile(file_id: string) {
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

export async function getUserSessions(user_id: number) {
  console.log('Fetching sessions for user:', user_id)
  return api<any[]>(`/users/${user_id}/sessions`)
}

export async function createNewConversation(user_id: number) {
  console.log('Creating new conversation for user:', user_id)
  return api<{ session_id: string; message: string }>(`/conversations/new?user_id=${user_id}`, {
    method: 'POST',
  })
}

export async function updateConversationTitle(
  session_id: string,
  user_id: number,
  title: string
) {
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
) {
  console.log('Fetching session messages:', { session_id, user_id, limit })
  return api<any[]>(
    `/chat/session/${encodeURIComponent(session_id)}/messages?user_id=${user_id}&limit=${limit}`
  )
}

export async function getAssistantName() {
  return api<{ name: string }>('/assistant/name')
}

export async function getSettings() {
  return api<{ brave_enabled: boolean; brave_configured: boolean }>('/settings')
}

export async function postFeedback(
  episode_id: string | null,
  message_id: string,
  kind: string,
  comment: string | null
) {
  return api<any>('/feedback', {
    method: 'POST',
    body: JSON.stringify({ episode_id, message_id, kind, comment }),
  })
}

export async function postCorrection(
  episode_id: string | null,
  message_id: string,
  correction_text: string
) {
  return api<any>('/correction', {
    method: 'POST',
    body: JSON.stringify({ episode_id, message_id, correction_text }),
  })
}

export async function transcribeAudio(audioBlob: Blob) {
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

export async function getOGPreview(url: string) {
  return api<any>(`/og-preview?url=${encodeURIComponent(url)}`)
}