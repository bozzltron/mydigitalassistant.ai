import { Show, For } from 'solid-js'

interface Slot {
  key: string
  value: string
  confidence: number
  sourceType: string
  updatedAt: string
}

interface Frame {
  id: number
  name: string
  type: string
  confidence: number
  essential: boolean
  priority: number
  ownerUserId: number | null
  sourceType: string | null
  sourceUrl: string | null
  sourceReliability: number | null
  createdAt: string
  updatedAt: string
  slots: Slot[]
  associations: Array<{id: number, relationType: string, targetFrameId: number}>
}

interface FrameDetailProps {
  frame?: Frame | null
  onBack?: () => void
}

export default function FrameDetail(props: FrameDetailProps) {
  return (
    <Show when={props.frame} fallback={
      <div class="frame-detail">
        <div class="frame-header">
          <h3>No Frame Selected</h3>
        </div>
        
        <div class="frame-content">
          <p>Select a frame from the graph to view details.</p>
        </div>
      </div>
    }>
      {(frame: Frame) => (
        <div class="frame-detail">
          <div class="frame-header">
            <button onClick={props.onBack} class="back-button">← Back</button>
            <h3>{frame.name}</h3>
            <span class={`frame-type ${frame.type}`}>{frame.type}</span>
          </div>
          
          <div class="frame-content">
            <div class="frame-info">
              <div class="info-item">
                <strong>ID:</strong> {frame.id}
              </div>
              
              <div class="info-item">
                <strong>Confidence:</strong> {(frame.confidence * 100).toFixed(1)}%
              </div>
              
              <Show when={frame.sourceUrl}>
                <div class="info-item">
                  <strong>Source:</strong> 
                  <a href={frame.sourceUrl} target="_blank" rel="noopener noreferrer">
                    {frame.sourceUrl}
                  </a>
                </div>
              </Show>
              
              <div class="info-item">
                <strong>Created:</strong> {new Date(frame.createdAt).toLocaleString()}
              </div>
            </div>
            
            <div class="frame-slots">
              <h4>Slots</h4>
              
              {frame.slots.length > 0 ? (
                <table class="slots-table">
                  <thead>
                    <tr>
                      <th>Key</th>
                      <th>Value</th>
                      <th>Confidence</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For each={frame.slots}>{slot => (
                      <tr >
                        <td><code>{slot.key}</code></td>
                        <td>{slot.value}</td>
                        <td>{(slot.confidence * 100).toFixed(1)}%</td>
                        <td>{new Date(slot.updatedAt).toLocaleString()}</td>
                      </tr>
                    )}</For>
                  </tbody>
                </table>
              ) : (
                <p>No slots defined for this frame.</p>
              )}
            </div>
            
            <div class="frame-associations">
              <h4>Associations</h4>
              
              {frame.associations.length > 0 ? (
                <ul class="associations-list">
                  <For each={frame.associations}>{assoc => (
                    <li >
                      <strong>{assoc.relationType}</strong> → Frame {assoc.targetFrameId}
                    </li>
                  )}</For>
                </ul>
              ) : (
                <p>No associations defined for this frame.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </Show>
  )
}