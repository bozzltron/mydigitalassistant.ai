export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  id: string
  meta?: MessageMeta
}

export interface MessageMeta {
  task_type?: string
  memory_context?: string
  citations?: string[]
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
  ogData?: Record<string, OgData>
  media?: MediaContent[]
}

export interface ExtractionSummary {
  slots_applied: number
  associations_created: number
  conflicts_created: number
  frame_ids: number[]
  slots: Slot[]
}

export interface Slot {
  frame_name: string
  key: string
  value: string
  conflict?: boolean
}

export interface SearchInfo {
  backend: string
  query: string
  engines?: string[]
  video_results?: YouTubeVideo[]
}

export interface OgData {
  title?: string
  description?: string
  image?: string
  site_name?: string
}

export interface MediaContent {
  type: 'image' | 'video' | 'youtube' | 'preview-card'
  url: string
  thumbnail?: string
  title?: string
  description?: string
  sourceUrl?: string
  aspectRatio?: number
  width?: number
  height?: number
}

export interface YouTubeVideo {
  videoId: string
  title: string
  channelTitle?: string
  thumbnailUrl: string
  url: string
  publishedAt?: string
  duration?: string
}

export interface SearchResultItem {
  title: string
  url: string
  snippet: string
  engine: string
  thumbnail?: string
}

export interface SearchResponse {
  results: SearchResultItem[]
  video_results?: YouTubeVideo[]
}

export interface QueuedMessage {
  id: string
  message: string
  timestamp: number
}

export interface Frame {
  id: number
  name: string
  type: string
  confidence: number
  essential: boolean
  priority: number
  owner_user_id: number | null
  source_type: string | null
  source_url: string | null
  source_reliability: number | null
  embedding_model: string | null
  created_at: string
  updated_at: string
}

export interface Association {
  id: number
  from_frame_id: number
  to_frame_id: number
  relation_type: string
  confidence: number
  essential: boolean
  priority: number
  source_type: string | null
  source_url: string | null
  source_reliability: number | null
  embedding_model: string | null
  created_at: string
}

export interface Conflict {
  frame_id: number
  frame_name: string
  slot_key: string
  slot_value: string
  confidence: number
  new_value: string
  new_confidence: number
  created_at: string
}

export interface SearchResult {
  id: number
  query: string
  results: Array<{
    url: string
    title: string
    content: string
    relevance: number
  }>
}

export interface FileEntry {
  id: string
  name: string
  size: number
  type: string
  created_at: string
  updated_at: string
}

export interface User {
  id: number
  name: string
  created_at: string
}

export interface Session {
  id: string
  user_id: number
  title: string | null
  created_at: string
  updated_at: string
  episode_count: number
  last_message: string | null
}

export interface DeletedSession {
  id: string
  user_id: number
  title: string | null
  created_at: string
  updated_at: string
  deleted_at: string
  episode_count: number
  last_activity: string | null
  first_user_message: string | null
}

export interface SessionMessage {
  role: string
  content: string
  timestamp?: string
}

export interface AttachedFile {
  name: string
  ext: string
  preview: string
  content: string
  text: string
  key_entities: string[]
  open_questions: string[]
}

export interface ChatRequestConsent {
  search_consent: boolean
}

export interface ChatResponse {
  session_id: string
  response: string
  task_type: string
  memory_context?: string
  citations?: string[]
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
}