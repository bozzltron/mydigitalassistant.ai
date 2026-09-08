import { Show, createSignal } from 'solid-js'

interface TracePanelProps {
  taskType?: string
  memory?: string
  citations?: string[]
  searchInfo?: any
}

export default function TracePanel(props: TracePanelProps) {
  const [isVisible, setIsVisible] = createSignal(false)
  
  return (
    <div class="trace-panel" style={{ display: isVisible() ? 'block' : 'none' }}>
      <div class="panel-header">
        <h3>Trace Information</h3>
        <button onClick={() => setIsVisible(!isVisible())}>
          {isVisible() ? 'Hide' : 'Show'} Trace
        </button>
      </div>
      
      <div class="trace-content">
        <Show when={props.taskType}>
          <div class="trace-section">
            <strong>Task Type:</strong> {props.taskType}
          </div>
        </Show>
        
        <Show when={props.memory}>
          <div class="trace-section">
            <strong>Memory Context:</strong>
            <div class="memory-context">{props.memory}</div>
          </div>
        </Show>
        
        <Show when={props.citations && props.citations.length > 0}>
          <div class="trace-section">
            <strong>Citations:</strong>
            <div class="citations-list">
              {props.citations?.map((citation, i) => (
                <a key={i} href={citation} target="_blank" rel="noopener noreferrer">
                  [{i + 1}] {citation}
                </a>
              ))}
            </div>
          </div>
        </Show>
        
        <Show when={props.searchInfo}>
          <div class="trace-section">
            <strong>Search Results:</strong>
            <div class="search-results">
              {/* Search result display would go here */}
              <p>Search results would be displayed here</p>
            </div>
          </div>
        </Show>
      </div>
    </div>
  )
}