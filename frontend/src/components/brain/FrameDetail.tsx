import { Show, For, createSignal, createEffect } from 'solid-js'
import type { JSX } from 'solid-js'
import type { BrainAssociation, BrainConflict, BrainFrame, BrainSlot } from '../../types'
import { esc, typeColor } from './brainLib'

interface FrameDetailProps {
  frame?: BrainFrame
  onBack?: () => void
  conflicts: BrainConflict[]
  onConflictResolved?: () => void
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

export default function FrameDetail(props: FrameDetailProps) {
  const [slots, setSlots] = createSignal<BrainSlot[]>([])
  const [associations, setAssociations] = createSignal<BrainAssociation[]>([])
  const [loadingDetail, setLoadingDetail] = createSignal(false)

  async function loadFrameDetail(f: BrainFrame) {
    setLoadingDetail(true)
    try {
      const [slotsRes, assocRes] = await Promise.all([
        fetch(`/memory/frames/${f.id}/slots`),
        fetch(`/memory/frames/${f.id}/associations`),
      ])
      if (!slotsRes.ok || !assocRes.ok) throw new Error('Failed to load frame detail')
      setSlots(await slotsRes.json())
      setAssociations(await assocRes.json())
    } catch (err) {
      console.error('Failed to load frame detail:', err)
      setSlots([])
      setAssociations([])
    } finally {
      setLoadingDetail(false)
    }
  }

  // Frames from /memory/frames do not include slots/associations unless
  // explicitly joined, so fetch the per-frame detail when the frame changes.
  createEffect(() => {
    const f = props.frame
    if (!f) {
      setSlots([])
      setAssociations([])
      return
    }
    void loadFrameDetail(f)
  })

  const handleResolve = async (conflict: BrainConflict, value: string | null) => {
    try {
      const params = new URLSearchParams({ value: value ?? '' })
      const res = await fetch(`/memory/conflicts/${conflict.id}/resolve?${params.toString()}`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error('Resolve failed')
      props.onConflictResolved?.()
    } catch (err) {
      console.error('Failed to resolve conflict:', err)
    }
  }

  return (
    <Show
      when={props.frame}
      fallback={
        <div class="frame-detail">
          <div class="frame-header">
            <h3>No Frame Selected</h3>
          </div>
          <div class="frame-content">
            <p>Select a frame from the graph to view details.</p>
          </div>
        </div>
      }
    >
      {(frame) => {
        const f = frame()
        const frameConflictsFor = f
          ? props.conflicts.filter(c => c.frame_id === f.id)
          : []
        const conf = Math.round((f.confidence || 0.5) * 100)
        const pri = Math.round((f.priority || 0.5) * 100)

        return (
          <div class="frame-detail">
            <div class="frame-header">
              <button onClick={props.onBack} class="back-button">← Back</button>
              <h3>{esc(f.name)}</h3>
              <span class={`frame-type ${f.type}`} style={{ background: typeColor(f.type) }}>
                {f.type}
              </span>
            </div>

            <div class="frame-content">
              <div class="frame-info">
                <div class="info-item">
                  <strong>ID:</strong> {f.id}
                </div>
                <div class="info-item">
                  <strong>Confidence:</strong> {conf}%
                  {confBar(f.confidence)}
                </div>
                <div class="info-item">
                  <strong>Priority:</strong> {pri}%
                  {confBar(f.priority)}
                </div>
                <Show when={f.essential}>
                  <div class="info-item essential-badge">
                    <span style={{ color: 'var(--warning)' }}>⬢ Essential</span>
                  </div>
                </Show>
                <Show when={f.source_url}>
                  <div class="info-item">
                    <strong>Source:</strong>
                    <a href={f.source_url ?? undefined} target="_blank" rel="noopener noreferrer">
                      {esc(f.source_url)}
                    </a>
                  </div>
                </Show>
                <Show when={f.source_type}>
                  <div class="info-item">
                    <strong>Source Type:</strong> {esc(f.source_type)}
                  </div>
                </Show>
                <Show when={f.source_reliability != null}>
                  <div class="info-item">
                    <strong>Source Reliability:</strong> {Math.round((f.source_reliability || 0) * 100)}%
                  </div>
                </Show>
                <Show when={f.created_at}>
                  <div class="info-item">
                    <strong>Created:</strong> {fmtTs(f.created_at)}
                  </div>
                </Show>
                <Show when={f.updated_at}>
                  <div class="info-item">
                    <strong>Updated:</strong> {fmtTs(f.updated_at)}
                  </div>
                </Show>
              </div>

              <div class="frame-slots">
                <h4>Slots</h4>
                <Show when={loadingDetail()}>
                  <p>Loading slots…</p>
                </Show>
                <Show when={!loadingDetail() && slots().length > 0}>
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
                      <For each={slots()}>
                        {(slot) => (
                          <tr>
                            <td><code>{esc(slot.key)}</code></td>
                            <td>{esc(slot.value)}</td>
                            <td>{Math.round((slot.confidence || 0.5) * 100)}%</td>
                            <td>{slot.essential ? '⬢' : ''}</td>
                            <td>{Math.round((slot.priority || 0.5) * 100)}%</td>
                            <td>{fmtTs(slot.updated_at)}</td>
                          </tr>
                        )}
                      </For>
                    </tbody>
                  </table>
                </Show>
                <Show when={!loadingDetail() && slots().length === 0}>
                  <p>No slots defined for this frame.</p>
                </Show>
              </div>

              <Show when={frameConflictsFor.length > 0}>
                <div class="frame-conflicts">
                  <h4>Pending Conflicts</h4>
                  <ul class="conflicts-list">
                    <For each={frameConflictsFor}>
                      {(c) => (
                        <li class="conflict-item">
                          <div class="conflict-line">
                            <span class="conflict-key">{esc(c.slot_key)}:</span>
                            <span class="conflict-old">{esc(c.existing_value ?? '∅')}</span> →
                            <span class="conflict-new">{esc(c.new_value ?? '∅')}</span>
                          </div>
                          <div class="conflict-actions">
                            <button class="conflict-btn keep-old" onClick={() => handleResolve(c, c.existing_value)}>Keep existing</button>
                            <button class="conflict-btn use-new" onClick={() => handleResolve(c, c.new_value)}>Use new</button>
                          </div>
                        </li>
                      )}
                    </For>
                  </ul>
                </div>
              </Show>

              <div class="frame-associations">
                <h4>Associations</h4>
                <Show when={!loadingDetail() && associations().length > 0}>
                  <ul class="associations-list">
                    <For each={associations()}>
                      {(assoc) => (
                        <li>
                          <strong>{esc(assoc.relation_type)}</strong> → Frame {assoc.to_frame_id}
                          {assoc.confidence != null && (
                            <span class="assoc-confidence"> ({Math.round(assoc.confidence * 100)}%)</span>
                          )}
                        </li>
                      )}
                    </For>
                  </ul>
                </Show>
                <Show when={!loadingDetail() && associations().length === 0}>
                  <p>No associations defined for this frame.</p>
                </Show>
              </div>
            </div>
          </div>
        )
      }}
    </Show>
  )
}