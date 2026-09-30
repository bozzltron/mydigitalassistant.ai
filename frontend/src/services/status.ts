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

interface TurnEntry {
  status: [() => TurnStatus | null, (v: TurnStatus | null) => void]
  polling: [() => boolean, (v: boolean) => void]
  /** Local 1s tick that keeps `elapsed_s` moving; not a network poll. */
  interval: ReturnType<typeof setInterval> | null
  startedAt: number
}

const turnStatusMap = new Map<string, TurnEntry>()
let activeTurnId: string | null = null

function createEntry(): TurnEntry {
  const [status, setStatus] = createSignal<TurnStatus | null>(null)
  const [polling, setPolling] = createSignal(false)
  return { status: [status, setStatus], polling: [polling, setPolling], interval: null, startedAt: 0 }
}

function getOrCreateTurnStatus(turnId: string): TurnEntry {
  let entry = turnStatusMap.get(turnId)
  if (!entry) {
    entry = createEntry()
    turnStatusMap.set(turnId, entry)
  }
  return entry
}

export function clearTurnStatus(turnId: string): void {
  const entry = turnStatusMap.get(turnId)
  if (entry?.interval) clearInterval(entry.interval)
  turnStatusMap.delete(turnId)
}

/**
 * Mark a turn as running. Progress itself arrives on the SSE stream via
 * `setStreamStage`; this only seeds the initial state and starts the local
 * elapsed-time tick. The old implementation also polled `/chat/status/:id`
 * every 600ms, which duplicated the stream's stage events on the hot path.
 */
export function beginTurnStatus(
  turnId: string,
  stage = 'queued',
  detail = STAGE_LABELS[stage] ?? stage
): void {
  const entry = getOrCreateTurnStatus(turnId)
  if (entry.interval) clearInterval(entry.interval)

  activeTurnId = turnId
  entry.startedAt = Date.now()
  entry.polling[1](true)
  entry.status[1]({ stage, detail, elapsed_s: 0, done: false })

  entry.interval = setInterval(() => {
    const current = entry.status[0]()
    if (!current || current.done) return
    entry.status[1]({
      ...current,
      elapsed_s: Math.round((Date.now() - entry.startedAt) / 1000),
    })
  }, 1000)
}

/** Update a turn's status directly from the SSE stream (stage events). */
export function setStreamStage(turnId: string, stage: string, detail?: string): void {
  const entry = getOrCreateTurnStatus(turnId)
  const current = entry.status[0]()
  entry.status[1]({
    stage,
    detail: detail || STAGE_LABELS[stage] || stage,
    elapsed_s: current?.elapsed_s ?? 0,
    done: false,
  })
}

export function endTurnStatus(turnId?: string): void {
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
    turnStatusMap.delete(targetTurnId)
  }

  if (activeTurnId === targetTurnId) {
    activeTurnId = null
  }
}

export function useTurnStatus(turnId?: () => string | undefined) {
  const currentTurnId = createMemo(() => turnId?.())
  const entry = createMemo(() => {
    const id = currentTurnId()
    if (id) return getOrCreateTurnStatus(id)
    return activeTurnId ? getOrCreateTurnStatus(activeTurnId) : null
  })

  onCleanup(() => {
    const id = currentTurnId()
    if (id && !turnStatusMap.get(id)?.interval) {
      clearTurnStatus(id)
    }
  })

  const turnStatus = createMemo(() => entry()?.status[0]() ?? null)
  const isPolling = createMemo(() => entry()?.polling[0]() ?? false)

  return {
    turnStatus,
    isPolling,
  }
}
