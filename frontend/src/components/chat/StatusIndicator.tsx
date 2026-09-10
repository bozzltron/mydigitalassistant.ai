import { createMemo, Show } from 'solid-js'
import { useTurnStatus, getStageLabel } from '../../services/status'

export default function StatusIndicator() {
  const { turnStatus, isPolling } = useTurnStatus()

  const label = createMemo(() => {
    const status = turnStatus()
    if (!status || !isPolling()) return ''
    return getStageLabel(status.stage, status.detail)
  })

  const elapsed = createMemo(() => {
    const status = turnStatus()
    if (!status) return ''
    return `${Math.round(status.elapsed_s)}s`
  })

  const isDone = createMemo(() => {
    const status = turnStatus()
    return status?.done === true
  })

  return (
    <Show when={isPolling() && !isDone()}>
      <div class="status-indicator">
        <span class="status-spinner" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10" stroke-opacity="0.25" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke-opacity="1">
              <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="1s" repeatCount="indefinite" />
            </path>
          </svg>
        </span>
        <span class="status-label">{label()}</span>
        <span class="status-elapsed">{elapsed()}</span>
      </div>
    </Show>
  )
}