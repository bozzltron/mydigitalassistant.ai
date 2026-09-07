/**
 * Typed fetch wrapper for API endpoints.
 * Uses Zod schemas for request/response validation.
 */
import { z } from 'zod'

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
    slots: z.array(z.any()), // Simplified - would be more specific in full implementation
  }).optional(),
  search_extraction_summary: z.object({ /* TODO: implement */ }).optional(),
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

// API functions with types
export async function postChat(message: string, session_id?: string, attached_files?: any[]) {
  // Validation would happen here using Zod schemas
  const requestBody = {
    user_id: 1,
    message,
    session_id,
    attached_files
  }

  console.log('Sending chat request:', requestBody)
  
  // Mock API call - in real implementation this would be fetch()
  return new Promise((resolve) => {
    setTimeout(() => resolve({
      session_id: session_id || 'test-session-123',
      response: `Echo: ${message}`,
      task_type: 'functional'
    }), 500)
  })
}

export async function getFrames(user_id: number) {
  // Validation would happen here using Zod schemas
  console.log('Fetching frames for user:', user_id)
  
  // Mock API call - in real implementation this would be fetch()
  return new Promise((resolve) => {
    setTimeout(() => resolve([]), 300)
  })
}

export async function getAssociations(frame_id: number) {
  // Validation would happen here using Zod schemas
  console.log('Fetching associations for frame:', frame_id)
  
  // Mock API call - in real implementation this would be fetch()
  return new Promise((resolve) => {
    setTimeout(() => resolve([]), 300)
  })
}

export async function getSearchResults(query: string, minRelevance?: number) {
  // Validation would happen here using Zod schemas
  console.log('Searching:', { query, minRelevance })
  
  // Mock API call - in real implementation this would be fetch()
  return new Promise((resolve) => {
    setTimeout(() => resolve({
      query,
      results: []
    }), 300)
  })
}

export async function listFiles() {
  console.log('Listing files')
  
  // Mock API call - in real implementation this would be fetch()
  return new Promise((resolve) => {
    setTimeout(() => resolve([]), 300)
  })
}

export async function postFileUpload(file: File) {
  console.log('Uploading file:', file.name)
  
  // Mock API call - in real implementation this would be fetch()
  return new Promise((resolve) => {
    setTimeout(() => resolve({
      id: 'file-123',
      name: file.name,
      size: file.size,
      type: file.type,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }), 500)
  })
}

export async function deleteFile(file_id: string) {
  console.log('Deleting file:', file_id)
  
  // Mock API call - in real implementation this would be fetch()
  return new Promise((resolve) => {
    setTimeout(() => resolve({ success: true }), 300)
  })
}