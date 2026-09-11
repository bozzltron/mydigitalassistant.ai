import { Show, For } from 'solid-js'
import type { Frame, Conflict } from '../../types'

interface FrameDetailProps {
  frame?: Frame | null
  onBack?: () => void
  conflicts?: Conflict[]
}

const TYPE_COLORS: Record<string, string> = {
  person: '#f78166',
  concept: '#d2a8ff',
  event: '#79c0ff',
  household: '#7ee787',
  entity: '#ffa657',
}

function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&')
    .replace(/</g, '<')
    .replace(/>/g, '>')
    .replace(/"/g, '"')
    .replace(/'/g, '&apos;')
}

function fmtTs(ts: string | null | undefined): string {
  if (!ts) return ''
  const d = new Date(String(ts).replace(' ', 'T') + 'Z')
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString()
}

function confBar(pct: number): JSX.Element {
  return (
    <div class="conf-bar">
      <div style={{ width: `${Math.round((pct || 0.5) * 100)}%` }}></div>
    </div>
  )
}

export default function FrameDetail(props: FrameDetailProps) {
  const frame = props.frame
  const conflicts = props.conflicts || []

  if (!frame) {
    return (
      <div class="frame-detail">
        <div class="frame-header">
          <h3>No Frame Selected</h3>
        </div>
        <div class="frame-content">
          <p>Select a frame from the graph to view details.</p>
        </div>
      </div>
    )
  }

  const frameConflicts = conflicts.filter(c => c.frame_id === frame.id)
  const conf = Math.round((frame.confidence || 0.5) * 100)
  const pri = Math.round((frame.priority || 0.5) * 100)

  return (
    <div class="frame-detail">
      <div class="frame-header">
        <button onClick={props.onBack} class="back-button">← Back</button>
        <h3>{esc(frame.name)}</h3>
        <span class={`frame-type ${frame.type}`} style={{ background: TYPE_COLORS[frame.type] || TYPE_COLORS.entity }}>
          {frame.type}
        </span>
      </div>

      <div class="frame-content">
        <div class="frame-info">
          <div class="info-item">
            <strong>ID:</strong> {frame.id}
          </div>
          <div class="info-item">
            <strong>Confidence:</strong> {conf}%
            {confBar(frame.confidence)}
          </div>
          <div class="info-item">
            <strong>Priority:</strong> {pri}%
            {confBar(frame.priority)}
          </div>
          <Show when={frame.essential}>
            <div class="info-item essential-badge">
              <span style={{ color: 'var(--warning)' }}>⬢ Essential</span>
            </div>
          </Show>
          <Show when={frame.sourceUrl}>
            <div class="info-item">
              <strong>Source:</strong>
              <a href={frame.sourceUrl} target="_blank" rel="noopener noreferrer">
                {esc(frame.sourceUrl)}
              </a>
            </div>
          </Show>
          <Show when={frame.sourceType}>
            <div class="info-item">
              <strong>Source Type:</strong> {esc(frame.sourceType)}
            </div>
          </Show>
          <Show when={frame.sourceReliability != null}>
            <div class="info-item">
              <strong>Source Reliability:</strong> {Math.round((frame.sourceReliability || 0) * 100)}%
            </div>
          </Show>
          <Show when={frame.createdAt}>
            <div class="info-item">
              <strong>Created:</strong> {fmtTs(frame.createdAt)}
            </div>
          </Show>
          <Show when={frame.updatedAt}>
            <div class="info-item">
              <strong>Updated:</strong> {fmtTs(frame.updatedAt)}
            </div>
          </Show>
        </div>

        <div class="frame-slots">
          <h4>Slots</h4>
          {frame.slots && frame.slots.length > 0 ? (
            <table class="slots-table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Value</th>
                  <th>Confidence</th>
                  <th>Essential</th>
                  <th>Priority</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                <For each={frame.slots}>
                  {(slot) => (
                    <tr>
                      <td><code>{esc(slot.key)}</code></td>
                      <td>{esc(slot.value)}</td>
                      <td>{Math.round((slot.confidence || 0.5) * 100)}%</td>
                      <td>{slot.essential ? '⬢' : ''}</td>
                      <td>{Math.round((slot.priority || 0.5) * 100)}%</td>
                      <td>{fmtTs(slot.updatedAt)}</td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
          ) : (
            <p>No slots defined for this frame.</p>
          )}
        </div>

        <Show when={frameConflicts.length > 0}>
          <div class="frame-conflicts">
            <h4>Pending Conflicts</h4>
            <ul class="conflicts-list">
              <For each={frameConflicts}>
                {(c) => (
                  <li class="conflict-item">
                    <div class="conflict-line">
                      <span class="conflict-key">{esc(c.slot_key)}:</span>
                      <span class="conflict-old">{esc(c.existing_value ?? '∅')}</span> →
                      <span class="conflict-new">{esc(c.new_value ?? '∅')}</span>
                    </div>
                    <div class="conflict-actions">
                      <button class="conflict-btn keep-old">Keep existing</button>
                      <button class="conflict-btn use-new">Use new</button>
                    </div>
                  </li>
                )}
              </For>
            </ul>
          </div>
        </Show>

        <div class="frame-associations">
          <h4>Associations</h4>
          {frame.associations && frame.associations.length > 0 ? (
            <ul class="associations-list">
              <For each={frame.associations}>
                {(assoc) => (
                  <li>
                    <strong>{esc(assoc.relationType || assoc.relation_type)}</strong> → Frame {assoc.toFrameId || assoc.to_frame_id}
                    {assoc.confidence != null && (
                      <span class="assoc-confidence"> ({Math.round(assoc.confidence * 100)}%)</span>
                    )}
                  </li>
                )}
              </For>
            </ul>
          ) : (
            <p>No associations defined for this frame.</p>
          )}
        </div>
      </div>
    </div>
  )
}