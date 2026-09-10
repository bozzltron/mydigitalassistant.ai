import { createSignal, onCleanup } from 'solid-js'

export interface TurnStatus {
  stage: string
  detail: string
  elapsed_s: number
  done: boolean
}

export const STAGE_LABELS: Record<string, string> = {
  queued: 'getting started',
  routing: 'reading your message',
  recall: 'checking my memory',
  correcting: 'updating what I know',
  learning: 'learning from our conversation',
  searching: 'searching the web',
  reasoning: 'thinking it through',
  responding: 'writing a reply',
  scheduled: 'managing your daily list',
}

export function getStageLabel(stage: string, detail?: string): string {
  return STAGE_LABELS[stage] || detail || stage
}

const turnStatusMap = new Map<string, {
  status: [() => TurnStatus | null, (v: TurnStatus | null) => void];
  polling: [() => boolean, (v: boolean) => void];
  interval: ReturnType<typeof setInterval> | null;
}>()

let activeTurnId: string | null = null

function getOrCreateTurnStatus(turnId: string) {
  let entry = turnStatusMap.get(turnId)
  if (!entry) {
    const [status, setStatus] = createSignal<TurnStatus | null>(null)
    const [polling, setPolling] = createSignal(false)
    entry = { status: [status, setStatus], polling: [polling, setPolling], interval: null }
    turnStatusMap.set(turnId, entry)
  }
  return entry
}

export function clearTurnStatus(turnId: string): void {
  turnStatusMap.delete(turnId)
}

export function useTurnStatus(turnId?: string) {
  if (turnId) {
    const entry = getOrCreateTurnStatus(turnId)
    onCleanup(() => {
      if (!turnStatusMap.get(turnId)?.interval) {
        clearTurnStatus(turnId)
      }
    })
    return {
      turnStatus: entry.status[0],
      isPolling: entry.polling[0],
    }
  }
  const entry = activeTurnId ? getOrCreateTurnStatus(activeTurnId) : (() => {
    const [status, setStatus] = createSignal<TurnStatus | null>(null)
    const [polling, setPolling] = createSignal(false)
    return { status: [status, setStatus], polling: [polling, setPolling], interval: null }
  })()
  return {
    turnStatus: entry.status[0],
    isPolling: entry.polling[0],
  }
}

export function startStatusPolling(turnId: string) {
  const entry = getOrCreateTurnStatus(turnId)

  if (entry.interval) {
    clearInterval(entry.interval)
  }

  activeTurnId = turnId
  entry.polling[1](true)
  entry.status[1]({
    stage: 'queued',
    detail: 'getting started',
    elapsed_s: 0,
    done: false,
  })

  entry.interval = setInterval(async () => {
    try {
      const response = await fetch(`/chat/status/${turnId}`)
      if (!response.ok) {
        if (response.status === 404) {
          stopStatusPolling(turnId)
        }
        return
      }

      const status: TurnStatus = await response.json()
      entry.status[1](status)

      if (status.done) {
        stopStatusPolling(turnId)
      }
    } catch (error) {
      console.warn('Status polling error:', error)
    }
  }, 600)
}

export function stopStatusPolling(turnId?: string) {
  const targetTurnId = turnId || activeTurnId
  if (!targetTurnId) return

  const entry = turnStatusMap.get(targetTurnId)
  if (entry) {
    if (entry.interval) {
      clearInterval(entry.interval)
      entry.interval = null
    }
    entry.polling[1](false)
    entry.status[1](null)
    clearTurnStatus(targetTurnId)
  }

  if (activeTurnId === targetTurnId) {
    activeTurnId = null
  }
}

export function getCurrentTurnId() {
  return activeTurnId
}

export function createTurnId(): string {
  return crypto.randomUUID()
}