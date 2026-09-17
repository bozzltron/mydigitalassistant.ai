import { createStore } from 'solid-js/store'

// Voice state type definition - matching original chat.html
export type VoiceStatus = 'idle' | 'listening' | 'processing' | 'speaking' | 'error'

export interface VoiceState {
  status: VoiceStatus
  transcript?: string
  isDictating: boolean
  isTtsSpeaking: boolean
}

// Create store for voice state (allows partial updates)
export const [voice, setVoice] = createStore<VoiceState>({
  status: 'idle',
  transcript: undefined,
  isDictating: false,
  isTtsSpeaking: false,
})

// Callback for external stop recording (registered by useVoiceRecording hook)
let stopRecordingCallback: (() => void) | null = null

export const registerStopRecording = (fn: () => void) => {
  stopRecordingCallback = fn
}

export const unregisterStopRecording = () => {
  stopRecordingCallback = null
}

export const stopRecording = () => {
  if (stopRecordingCallback) {
    stopRecordingCallback()
  }
}

// Voice actions - proper state machine
export const enterVoiceMode = () => {
  setVoice('status', 'listening')
}

export const exitVoiceMode = () => {
  setVoice('status', 'idle')
  setVoice('isDictating', false)
  setVoice('transcript', undefined)
}

export const startListening = () => {
  setVoice('status', 'listening')
}

export const startProcessing = () => {
  setVoice('status', 'processing')
}

export const startSpeaking = () => {
  setVoice('status', 'speaking')
}

export const setError = (error: string) => {
  setVoice('status', 'error')
  setVoice('transcript', error)
}

// Dictation mode (one-shot recording)
export const startDictation = () => {
  setVoice('status', 'listening')
  setVoice('isDictating', true)
}

export const endDictation = () => {
  setVoice('status', 'idle')
  setVoice('isDictating', false)
  setVoice('transcript', undefined)
}

// Helper to get computed state for UI
export const isListening = () => voice.status === 'listening'
export const isProcessing = () => voice.status === 'processing'
export const isSpeaking = () => voice.status === 'speaking'
export const isIdle = () => voice.status === 'idle'
export const isError = () => voice.status === 'error'
export const isTtsSpeaking = () => voice.isTtsSpeaking

// TTS speaking state setters
export const setTtsSpeaking = (speaking: boolean) => {
  setVoice('isTtsSpeaking', speaking)
}