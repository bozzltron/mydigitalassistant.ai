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
  /** True while assistant text is still arriving over the stream. */
  isStreaming?: boolean
  /** True for a message rendered in the pending-queue panel, not the transcript. */
  isQueued?: boolean
  /** Entry point that produced the message. */
  source?: 'voice' | 'text'
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
  /** Confidence the stored slot carries (0-1), used for inline grounding. */
  confidence?: number
}

export interface SearchInfo {
  backend: string
  query: string
  engines?: string[]
  results?: SearchResultItem[]
  video_results?: YouTubeVideo[]
  sensitivity?: SensitivityResult
  consent_required?: boolean
}

export interface SensitivityResult {
  level: 'safe' | 'sensitive' | 'ambiguous'
  reason: string
  categories: string[]
}

export interface OgData {
  title?: string
  description?: string
  image?: string
  site_name?: string
}

/** Response from `GET /og-preview`. `found` is false when no metadata was scraped. */
export interface OgPreviewResponse {
  url: string
  found: boolean
  title?: string
  description?: string
  image?: string
  site_name?: string
}

export interface MediaContent {
  // `preview-card` was dropped with the image grid: nothing constructs it, and
  // `MediaCard` has no branch for it, so it silently rendered as an image.
  type: 'image' | 'video' | 'youtube'
  /** Display URL (for search images, the reliable Brave-CDN thumbnail). */
  url: string
  /** Full-size image; often hotlink-blocked, so callers fall back to `url`. */
  fullUrl?: string
  thumbnail?: string
  title?: string
  description?: string
  sourceUrl?: string
}

export interface YouTubeVideo {
  // Wire shape from the backend search pipeline (snake_case, as serialized).
  video_id: string
  title: string
  channel_title?: string | null
  thumbnail_url?: string | null
  url?: string | null
  published_at?: string | null
  duration?: string | null
}

export interface SearchResultItem {
  title: string
  url: string
  /** Present on live search results; omitted from the persisted episode payload. */
  snippet?: string
  engine?: string
  thumbnail?: string | null
  /** Full-size image (Brave thumbnail.original); falls back to `thumbnail`. */
  image?: string | null
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
  /** 0/1 int from the API; treat as boolean with truthiness. */
  essential: number
  priority: number
  owner_user_id: number | null
  source_type: string | null
  source_url: string | null
  source_reliability: number | null
  embedding_model: string | null
  created_at: string | null
  updated_at: string | null
}

export interface Association {
  id: number
  from_frame_id: number
  to_frame_id: number
  relation_type: string
  confidence: number
  /** 0/1 int from the API; treat as boolean with truthiness. */
  essential: number
  priority: number
  source_type: string | null
  source_url: string | null
  source_reliability: number | null
  embedding_model: string | null
  created_at: string | null
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

/** Response from `POST /files/upload` (see `upload_file_to_memory`). */
export interface FileUploadResult {
  status: string
  file_name: string
  file_size: number
  file_ext: string
  content_preview: string
  key_entities: string[]
  open_questions: string[]
  frame_name: string
  frame_id: number
  parent_frame_id: number
  row_count: number
  row_frame_ids: number[]
}

export interface FileEntry {
  id: string
  name: string
  file_name?: string | null
  file_ext?: string | null
  file_size?: number | null
  type: string
  created_at: string
  updated_at: string
}

export interface User {
  id: number
  name: string
  created_at: string | null
}

/** Raw item from `GET /users/{id}/sessions`. */
export interface SessionSummary {
  id: string
  episode_count: number
  last_activity: string | null
  /** When the session was created; used to order never-used conversations. */
  created_at: string | null
  /**
   * Backend-computed display label, not a chat message: an explicit title if
   * one was set, else the first user message (truncated), else "Conversation N".
   */
  last_message: string
}

/** A conversation as the UI consumes it (see `sessionTitle`). */
export interface Session {
  id: string
  title: string
  episode_count: number
  last_activity: string | null
  created_at: string | null
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
  /** Persisted search/media payload for a search turn (see `search_info`). */
  search_info?: SearchInfo
}

export interface AttachedFile {
  name: string
  ext: string
  preview: string
  /**
   * Inline text for the legacy text-only path. Empty for the input bar, which
   * uploads the file first and sends `frame_id`/`frame_name` instead — embedding
   * content here is what corrupted binary files (a PDF read as UTF-8) before
   * they reached the server. There is no `content` field: it was a second copy
   * of `text` that nothing read.
   */
  text: string
  key_entities: string[]
  open_questions: string[]
  /** The uploaded file's frame, so the backend can reference it by identity. */
  frame_id?: number
  frame_name?: string
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