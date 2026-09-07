export interface Frame {
  id: number | null
  name: string
  type: string | null
  confidence: number
  essential: number
  priority: number
  embedding_model: string | null
  created_at: string | null
  updated_at: string | null
  deleted_at: string | null
  source_type: string | null
  source_url: string | null
  source_reliability: number | null
}

export interface Slot {
  id: number | null
  frame_id: number
  key: string
  value: string
  confidence: number
  essential: number
  priority: number
  source_type: string | null
  source_url: string | null
  source_episode_id: number | null
  updated_at: string | null
  last_strengthened_at: string | null
}

export interface Association {
  id: number | null
  from_frame_id: number
  to_frame_id: number
  relation_type: string | null
  confidence: number
  essential: number
  priority: number
  source_type: string | null
  source_url: string | null
  embedding_model: string | null
  created_at: string | null
}

export interface SearchResult {
  frame: Frame
  relevance: number
  slots_applied: number
  associations_created: number
  conflicts_created: number
}

export interface TopicSearchResponse {
  results: SearchResult[]
}