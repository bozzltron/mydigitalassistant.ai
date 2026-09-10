import { describe, it, expect, vi, beforeEach } from 'vitest'
import { voice, setVoice, enterVoiceMode, exitVoiceMode, startProcessing, startSpeaking, setError } from '../state/voice'

describe('voice state', () => {
  beforeEach(() => {
    setVoice({
      isListening: false,
      isProcessing: false,
      isSpeaking: false,
      status: 'idle',
    })
  })

  it('initializes in idle state', () => {
    const state = voice()
    expect(state.status).toBe('idle')
    expect(state.isListening).toBe(false)
    expect(state.isProcessing).toBe(false)
    expect(state.isSpeaking).toBe(false)
    expect(state.transcript).toBeUndefined()
  })

  describe('enterVoiceMode', () => {
    it('sets listening status', () => {
      enterVoiceMode()
      const state = voice()
      expect(state.isListening).toBe(true)
      expect(state.status).toBe('listening')
    })

    it('preserves other fields', () => {
      setVoice({ isListening: false, isProcessing: true, isSpeaking: false, status: 'processing', transcript: 'old' })
      enterVoiceMode()
      const state = voice()
      expect(state.isProcessing).toBe(true)
      expect(state.transcript).toBe('old')
    })
  })

  describe('exitVoiceMode', () => {
    it('resets to idle state', () => {
      setVoice({ isListening: true, isProcessing: true, isSpeaking: true, status: 'speaking', transcript: 'something' })
      exitVoiceMode()
      const state = voice()
      expect(state.isListening).toBe(false)
      expect(state.isProcessing).toBe(false)
      expect(state.isSpeaking).toBe(false)
      expect(state.status).toBe('idle')
      expect(state.transcript).toBeUndefined()
    })
  })

  describe('startProcessing', () => {
    it('transitions from listening to processing', () => {
      enterVoiceMode()
      startProcessing()
      const state = voice()
      expect(state.isListening).toBe(false)
      expect(state.isProcessing).toBe(true)
      expect(state.status).toBe('processing')
    })
  })

  describe('startSpeaking', () => {
    it('transitions from processing to speaking', () => {
      setVoice({ isListening: false, isProcessing: true, isSpeaking: false, status: 'processing' })
      startSpeaking()
      const state = voice()
      expect(state.isProcessing).toBe(false)
      expect(state.isSpeaking).toBe(true)
      expect(state.status).toBe('speaking')
    })
  })

  describe('setError', () => {
    it('sets error status and transcript', () => {
      setError('Microphone permission denied')
      const state = voice()
      expect(state.status).toBe('error')
      expect(state.transcript).toBe('Microphone permission denied')
    })

    it('preserves other state', () => {
      setVoice({ isListening: true, isProcessing: false, isSpeaking: false, status: 'listening' })
      setError('Error occurred')
      const state = voice()
      expect(state.isListening).toBe(true)
    })
  })
})