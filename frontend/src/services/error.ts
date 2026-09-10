/**
 * Error handling service for the assistant application.
 */
import { createSignal } from 'solid-js'

export interface AppError {
  id: string
  message: string
  timestamp: Date
  type: 'network' | 'api' | 'validation' | 'user' | 'unknown'
  context?: Record<string, unknown>
}

export const [errors, setErrors] = createSignal<AppError[]>([])
export const [hasError, setHasError] = createSignal(false)

export function addError(error: Omit<AppError, 'id' | 'timestamp'>) {
  const newError: AppError = {
    id: Date.now().toString(),
    message: error.message,
    timestamp: new Date(),
    type: error.type,
    context: error.context
  }
  
  setErrors(prev => [...prev, newError])
  setHasError(true)
}

export function removeError(id: string) {
  setErrors(prev => prev.filter(error => error.id !== id))
  if (errors().length <= 1) {
    setHasError(false)
  }
}

export function clearErrors() {
  setErrors([])
  setHasError(false)
}

// Check if we can reach the brain backend
export async function checkBackendStatus(): Promise<boolean> {
  try {
    // This would be a real API call to check backend status
    // For now, simulate success
    await new Promise(resolve => setTimeout(resolve, 100))
    return true
  } catch {
    return false
  }
}