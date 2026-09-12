import { For, Show } from 'solid-js'
import type { Frame, Slot, Association, Conflict } from '../../types'

interface TopicMatch {
  frame: Frame
  slots: Slot[]
  similarity: number | null
  associations: Association[]
  episodes: Array<{ role: string; content: string; timestamp: string }>
  conflicts: Conflict[]
}

interface SearchPanelProps {
  onResultClick?: (match: TopicMatch) => void
  searchQuery: string
  searchResults: TopicMatch[]
  isSearching: boolean
  searchError: string | null
  onClose: () => void
}

const TYPE_COLORS: Record<string, string> = {
  person: '#f78166',
  concept: '#d2a8ff',
  event: '#79c0ff',
  household: '#7ee787',
  entity: '#ffa657',
}

function frameNameById(id: number, frames: Frame[]): string {
  const n = frames.find(x => x.id === id)
  return n ? n.name : `#${id}`
}

function fmtTs(ts: string | null | undefined): string {
  if (!ts) return ''
  const d = new Date(String(ts).replace(' ', 'T') + 'Z')
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString()
}

function confBar(pct: number): JSX.Element {
  return (
    <div class="conf-bar">
      <div style={{ width: `${Math.round((pct || 0.5) * 100)}%` }} />
    </div>
  )
}

export default function SearchPanel(props: SearchPanelProps) {
  return (
    <div class="search-panel">
      <div class="topic-panel-header">
        <div class="topic-panel-title">Topic memory: {props.searchQuery}</div>
        <button class="topic-panel-close" onClick={() => props.onClose()}>×</button>
      </div>

      <Show when={props.searchError}>
        <div class="search-error">{props.searchError}</div>
      </Show>

      <div class="topic-results">
        <For each={props.searchResults}>
          {(match) => (
            <div class="topic-card" onClick={() => props.onResultClick?.(match)}>
              <div class="topic-card-head">
                <div class="topic-card-dot" style={{ background: TYPE_COLORS[match.frame.type] || TYPE_COLORS.entity }} />
                <div class="topic-card-name">{match.frame.name}</div>
                {match.similarity != null && (
                  <div class="topic-card-sim">{Math.round(match.similarity * 100)}% match</div>
                )}
              </div>

              <div class="topic-meta">
                {match.frame.type} · confidence {Math.round((match.frame.confidence || 0.5) * 100)}%
                · priority {Math.round((match.frame.priority || 0.5) * 100)}%
                {match.frame.essential ? ' · essential' : ''}
                {match.similarity == null ? ' · keyword match' : ''}
              </div>
              {confBar(match.frame.confidence)}

              {(match.slots || []).length > 0 && (
                <>
                  <div class="topic-section-title">Stored facts</div>
                  <For each={match.slots}>
                    {(s) => (
                      <div class="topic-slot">
                        <span class="k">{s.key}: </span>
                        {s.value}
                        {s.confidence != null && (
                          <span class="c"> {Math.round(s.confidence * 100)}%</span>
                        )}
                        {s.last_strengthened_at && (
                          <span class="c"> · last reinforced {fmtTs(s.last_strengthened_at)}</span>
                        )}
                      </div>
                    )}
                  </For>
                </>
              )}

              {(match.associations || []).length > 0 && (
                <>
                  <div class="topic-section-title">Connections</div>
                  <For each={match.associations}>
                    {(a) => {
                      const other = a.from_frame_id === match.frame.id ? a.to_frame_id : a.from_frame_id
                      const dir = a.from_frame_id === match.frame.id ? '→' : '←'
                      const pct = a.confidence != null ? ` (${Math.round(a.confidence * 100)}%)` : ''
                      return (
                        <div class="topic-assoc">
                          {dir} <span class="rel">{a.relation_type}</span> {frameNameById(other, [match.frame])}{pct}
                        </div>
                      )
                    }}
                  </For>
                </>
              )}

              {(match.episodes || []).length > 0 && (
                <>
                  <div class="topic-section-title">Conversations that touched this</div>
                  <For each={match.episodes.slice(0, 4)}>
                    {(ep) => (
                      <div class="topic-episode">
                        <span class="when">{fmtTs(ep.timestamp)}</span> [{ep.role}]: {ep.content.slice(0, 200)}...
                      </div>
                    )}
                  </For>
                </>
              )}

              {(match.conflicts || []).length > 0 && (
                <>
                  <div class="topic-section-title">Pending conflicts</div>
                  <For each={match.conflicts}>
                    {(c) => (
                      <div class="topic-conflict-line">
                        <span class="k">{c.slot_key}:</span>
                        <span class="old">{c.existing_value ?? '∅'}</span> →
                        <span class="new">{c.new_value ?? '∅'}</span>
                      </div>
                    )}
                  </For>
                </>
              )}
            </div>
          )}
        </For>

        <Show when={!props.isSearching && props.searchResults.length === 0 && props.searchQuery}>
          <div class="topic-empty">No results found for &ldquo;{props.searchQuery}&rdquo;</div>
        </Show>
      </div>
    </div>
  )
}