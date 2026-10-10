import { Show } from 'solid-js'
import type { MessageMeta } from '../../types/chat'

/**
 * The turn trace: what the agent knew and used for the most recent assistant
 * turn. It reads the turn's `meta` (never the prompt), so it costs the model
 * nothing.
 *
 * Live turns carry `task_type`, `memory_context`, `citations`, and `search_info`
 * on the `meta` event. A reloaded turn carries only what was persisted
 * (`search_info`), so the panel shows what it has and says so plainly rather
 * than rendering `-`.
 */
export default function TracePanel(props: {
  visible: boolean
  meta?: MessageMeta
  onClose: () => void
}) {
  return (
    <div id="trace-panel" class={`trace-panel${props.visible ? ' open' : ''}`}>
      <div class="trace-header">
        <span>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            class="trace-header-icon"
          >
            <line x1="18" x2="18" y1="20" y2="10" />
            <line x1="12" x2="12" y1="20" y2="4" />
            <line x1="6" x2="6" y1="20" y2="14" />
          </svg>
          Trace
        </span>
        <button class="trace-close" onClick={() => props.onClose()} id="trace-close">
          &times;
        </button>
      </div>
      <div class="trace-content">
        <Show
          when={props.meta}
          fallback={
            <div class="trace-empty">
              No turn yet. Send a message to see the memory the agent used.
            </div>
          }
        >
          {(meta) => {
            const citations = () => meta().citations ?? []
            return (
              <>
                <div class="trace-section">
                  <div class="trace-label">Task type</div>
                  <div class="trace-value" id="trace-task-type">
                    {meta().task_type || '—'}
                  </div>
                </div>
                <div class="trace-section">
                  <div class="trace-label">Memory context</div>
                  <div class="trace-value" id="trace-memory">
                    {meta().memory_context || 'Not captured for this turn.'}
                  </div>
                </div>
                <div class="trace-section">
                  <div class="trace-label">Citations</div>
                  <div class="trace-value" id="trace-citations">
                    {citations().length ? citations().join('\n') : 'None'}
                  </div>
                </div>
                <Show when={meta().search_info}>
                  {(si) => (
                    <div class="trace-section" id="trace-search-section">
                      <div class="trace-label">Search</div>
                      <div class="trace-value" id="trace-search-info">
                        {si().backend}
                        {si().query ? ` · ${si().query}` : ''}
                      </div>
                    </div>
                  )}
                </Show>
              </>
            )
          }}
        </Show>
      </div>
    </div>
  )
}
