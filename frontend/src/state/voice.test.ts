import { describe, it, expect, beforeEach } from 'vitest'
import { voice, setVoice, enterVoiceMode, exitVoiceMode, startProcessing, startSpeaking, setError, isListening, isProcessing, isSpeaking } from '../state/voice'

describe('voice state', () => {
  beforeEach(() => {
    setVoice({
      status: 'idle',
      transcript: undefined,
      isDictating: false,
      isTtsSpeaking: false,
    })
  })

  it('initializes in idle state', () => {
    expect(voice.status).toBe('idle')
    expect(isListening()).toBe(false)
    expect(isProcessing()).toBe(false)
    expect(isSpeaking()).toBe(false)
    expect(voice.transcript).toBeUndefined()
  })

  describe('enterVoiceMode', () => {
    it('sets listening status', () => {
      enterVoiceMode()
      expect(isListening()).toBe(true)
      expect(voice.status).toBe('listening')
    })

    it('preserves transcript and dictation fields', () => {
      setVoice({ status: 'processing', transcript: 'old', isDictating: true, isTtsSpeaking: false })
      enterVoiceMode()
      expect(voice.transcript).toBe('old')
      expect(voice.isDictating).toBe(true)
    })
  })

  describe('exitVoiceMode', () => {
    it('resets to idle state', () => {
      setVoice({ status: 'speaking', transcript: 'something', isDictating: true, isTtsSpeaking: false })
      exitVoiceMode()
      expect(isListening()).toBe(false)
      expect(isProcessing()).toBe(false)
      expect(isSpeaking()).toBe(false)
      expect(voice.status).toBe('idle')
      expect(voice.transcript).toBeUndefined()
    })
  })

  describe('startProcessing', () => {
    it('transitions from listening to processing', () => {
      enterVoiceMode()
      startProcessing()
      expect(isListening()).toBe(false)
      expect(isProcessing()).toBe(true)
      expect(voice.status).toBe('processing')
    })
  })

  describe('startSpeaking', () => {
    it('transitions from processing to speaking', () => {
      setVoice({ status: 'processing', transcript: undefined, isDictating: false, isTtsSpeaking: false })
      startSpeaking()
      expect(isProcessing()).toBe(false)
      expect(isSpeaking()).toBe(true)
      expect(voice.status).toBe('speaking')
    })
  })

  describe('setError', () => {
    it('sets error status and transcript', () => {
      setError('Microphone permission denied')
      expect(voice.status).toBe('error')
      expect(voice.transcript).toBe('Microphone permission denied')
    })

    it('preserves dictation and tts fields', () => {
      setVoice({ status: 'listening', transcript: undefined, isDictating: true, isTtsSpeaking: true })
      setError('Error occurred')
      expect(voice.isDictating).toBe(true)
      expect(voice.isTtsSpeaking).toBe(true)
    })
  })
})