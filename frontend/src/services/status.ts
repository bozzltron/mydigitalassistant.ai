import { createSignal, createEffect, onCleanup } from 'solid-js'
import { api } from './api'

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

let pollInterval: ReturnType<typeof setInterval> | null = null
let currentTurnId: string | null = null

const [turnStatus, setTurnStatus] = createSignal<TurnStatus | null>(null)
const [isPolling, setIsPolling] = createSignal(false)

export function useTurnStatus() {
  return {
    turnStatus,
    isPolling,
  }
}

export function startStatusPolling(turnId: string) {
  if (pollInterval) {
    clearInterval(pollInterval)
  }
  
  currentTurnId = turnId
  setIsPolling(true)
  setTurnStatus({
    stage: 'queued',
    detail: 'getting started',
    elapsed_s: 0,
    done: false,
  })

  pollInterval = setInterval(async () => {
    try {
      const response = await fetch(`/chat/status/${turnId}`)
      if (!response.ok) {
        if (response.status === 404) {
          stopStatusPolling()
        }
        return
      }
      
      const status: TurnStatus = await response.json()
      setTurnStatus(status)
      
      if (status.done) {
        stopStatusPolling()
      }
    } catch (error) {
      console.warn('Status polling error:', error)
    }
  }, 600)
}

export function stopStatusPolling() {
  if (pollInterval) {
    clearInterval(pollInterval)
    pollInterval = null
  }
  currentTurnId = null
  setIsPolling(false)
  setTurnStatus(null)
}

export function getCurrentTurnId() {
  return currentTurnId
}

export function createTurnId(): string {
  return crypto.randomUUID()
}