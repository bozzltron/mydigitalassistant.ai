import { createMemo, Show } from 'solid-js'
import { getStageLabel } from '../../services/status'

interface StatusIndicatorProps {
  turnStatus: () => import('../../services/status').TurnStatus | null
  isPolling: () => boolean
}

export default function StatusIndicator(props: StatusIndicatorProps) {
  const label = createMemo(() => {
    const status = props.turnStatus()
    if (!status || !props.isPolling()) return ''
    return getStageLabel(status.stage, status.detail)
  })

  const elapsed = createMemo(() => {
    const status = props.turnStatus()
    if (!status) return ''
    return `${Math.round(status.elapsed_s)}s`
  })

  const isDone = createMemo(() => {
    const status = props.turnStatus()
    return status?.done === true
  })

  const dotClass = createMemo(() => {
    const status = props.turnStatus()
    if (!status) return 'voice-dot'
    if (status.stage === 'searching' || status.stage === 'reasoning') return 'voice-dot processing'
    return 'voice-dot listening'
  })

  return (
    <Show when={props.isPolling() && !isDone()}>
      <div class="status-indicator voice-status-bar active">
        <span class={dotClass()} aria-hidden="true" />
        <span class="status-label">{label()}</span>
        <span class="status-elapsed">{elapsed()}</span>
      </div>
    </Show>
  )
}