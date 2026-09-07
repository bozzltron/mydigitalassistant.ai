import { createSignal } from 'solid-js'

// Voice state type definition
export interface VoiceState {
  isListening: boolean
  isProcessing: boolean
  isSpeaking: boolean
  status: 'idle' | 'listening' | 'processing' | 'speaking' | 'error'
  transcript?: string
}

// Create signal for voice state
export const [voice, setVoice] = createSignal<VoiceState>({
  isListening: false,
  isProcessing: false,
  isSpeaking: false,
  status: 'idle'
})

// Voice actions
export const enterVoiceMode = () => {
  setVoice(prev => ({
    ...prev,
    isListening: true,
    status: 'listening'
  }))
}

export const exitVoiceMode = () => {
  setVoice(prev => ({
    ...prev,
    isListening: false,
    isProcessing: false,
    isSpeaking: false,
    status: 'idle',
    transcript: undefined
  }))
}

export const startProcessing = () => {
  setVoice(prev => ({
    ...prev,
    isListening: false,
    isProcessing: true,
    status: 'processing'
  }))
}

export const startSpeaking = () => {
  setVoice(prev => ({
    ...prev,
    isProcessing: false,
    isSpeaking: true,
    status: 'speaking'
  }))
}

export const setError = (error: string) => {
  setVoice(prev => ({
    ...prev,
    status: 'error',
    transcript: error
  }))
}