/**
 * Shared types for the Brain Observatory (frontend/src/components/brain).
 *
 * These match the shapes returned by the backend memory API verbatim
 * (`/memory/frames`, `/memory/frames/{id}/slots`, `/memory/associations`,
 * `/memory/conflicts`, `/memory/frames/{id}/associations`, `/memory/search`).
 */

export interface BrainFrame {
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
  deleted_at: string | null
  /** Only populated when explicitly joined (e.g. topic search). */
  slots?: BrainSlot[]
  associations?: BrainAssociation[]
}

export interface BrainSlot {
  id: number
  frame_id: number
  key: string
  value: string
  confidence: number
  essential: number
  priority: number
  source_type: string | null
  source_url: string | null
  source_reliability: number | null
  source_episode_id: number | null
  updated_at: string | null
  last_strengthened_at: string | null
}

export interface BrainAssociation {
  id: number
  from_frame_id: number
  to_frame_id: number
  relation_type: string
  confidence: number
  essential: number
  priority: number
  source_type: string | null
  source_url: string | null
  source_reliability: number | null
  embedding_model: string | null
  created_at: string | null
}

export interface BrainConflict {
  id: number
  frame_id: number
  frame_name?: string
  slot_key: string
  existing_value: string | null
  new_value: string | null
  resolved_value: string | null
  status: string
  created_at: string | null
  resolved_at: string | null
}

export interface BrainTopicMatch {
  frame: BrainFrame
  slots: BrainSlot[]
  similarity: number | null
  associations: BrainAssociation[]
  episodes: Array<{ role: string; content: string; timestamp: string }>
  conflicts: BrainConflict[]
}

export interface BrainTopicSearchResponse {
  query: string
  semantic_search: boolean
  backend_rev: number
  matches: BrainTopicMatch[]
  summary: string
}

/** A frame prepared for graph layout (2D d3 / 3D force-graph). */
export interface GraphNode {
  id: number
  name: string
  type: string
  confidence: number
  priority: number
  essential: number
  hasConflict: boolean
  slots?: BrainSlot[]
  x?: number
  y?: number
  vx?: number
  vy?: number
  fx?: number | null
  fy?: number | null
}

/** An association flattened for graph layout. */
export interface GraphLink {
  source: number
  target: number
  relationType: string
  confidence: number
}