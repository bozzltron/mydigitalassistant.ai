import { createSignal, onCleanup, createMemo } from 'solid-js'

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

export function useTurnStatus(turnId?: () => string | undefined) {
  const currentTurnId = createMemo(() => turnId?.())
  const entry = createMemo(() => {
    const id = currentTurnId()
    if (id) {
      return getOrCreateTurnStatus(id)
    }
    return activeTurnId ? getOrCreateTurnStatus(activeTurnId) : (() => {
      const [status, setStatus] = createSignal<TurnStatus | null>(null)
      const [polling, setPolling] = createSignal(false)
      return { status: [status, setStatus], polling: [polling, setPolling], interval: null }
    })()
  })

  onCleanup(() => {
    const id = currentTurnId()
    if (id && !turnStatusMap.get(id)?.interval) {
      clearTurnStatus(id)
    }
  })

  const turnStatus = createMemo(() => entry().status[0]())
  const isPolling = createMemo(() => entry().polling[0]())

  return {
    turnStatus,
    isPolling,
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

/**
 * Update a turn's status directly from the SSE stream (stage events).
 *
 * Streaming carries live pipeline stages, so the UI can show progress without
 * waiting for the next /chat/status poll. Falls back to creating the turn
 * entry if polling never started.
 */
export function setStreamStage(turnId: string, stage: string, detail?: string): void {
  const entry = getOrCreateTurnStatus(turnId)
  entry.status[1]({
    stage,
    detail: detail || stage,
    elapsed_s: 0,
    done: false,
  })
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