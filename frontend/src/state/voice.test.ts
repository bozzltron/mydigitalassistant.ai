import { describe, it, expect } from 'vitest'
import { voice, setVoice, isVoiceModeActive, isListening, isProcessing, isSpeaking, isIdle, isTtsSpeaking } from './voice'

describe('voice state helpers', () => {
  beforeEach(() => {
    setVoice({
      status: 'idle',
      transcript: undefined,
      isDictating: false,
      isTtsSpeaking: false,
    })
  })

  describe('isVoiceModeActive', () => {
    it('returns true for listening status', () => {
      setVoice({ ...voice, status: 'listening' })
      expect(isVoiceModeActive()).toBe(true)
    })

    it('returns true for processing status', () => {
      setVoice({ ...voice, status: 'processing' })
      expect(isVoiceModeActive()).toBe(true)
    })

    it('returns false for idle status', () => {
      setVoice({ ...voice, status: 'idle' })
      expect(isVoiceModeActive()).toBe(false)
    })

    it('returns false for speaking status', () => {
      setVoice({ ...voice, status: 'speaking' })
      expect(isVoiceModeActive()).toBe(false)
    })

    it('returns false for error status', () => {
      setVoice({ ...voice, status: 'error' })
      expect(isVoiceModeActive()).toBe(false)
    })
  })

  describe('isListening', () => {
    it('returns true only for listening status', () => {
      setVoice({ ...voice, status: 'listening' })
      expect(isListening()).toBe(true)
      
      setVoice({ ...voice, status: 'processing' })
      expect(isListening()).toBe(false)
    })
  })

  describe('isProcessing', () => {
    it('returns true only for processing status', () => {
      setVoice({ ...voice, status: 'processing' })
      expect(isProcessing()).toBe(true)
      
      setVoice({ ...voice, status: 'listening' })
      expect(isProcessing()).toBe(false)
    })
  })

  describe('isSpeaking', () => {
    it('returns true for speaking status', () => {
      setVoice({ ...voice, status: 'speaking' })
      expect(isSpeaking()).toBe(true)
    })

    it('returns true when TTS is speaking', () => {
      setVoice({ ...voice, status: 'idle', isTtsSpeaking: true })
      expect(isSpeaking()).toBe(true)
    })

    it('returns false otherwise', () => {
      setVoice({ ...voice, status: 'idle' })
      expect(isSpeaking()).toBe(false)
    })
  })

  describe('isIdle', () => {
    it('returns true only for idle status', () => {
      setVoice({ ...voice, status: 'idle' })
      expect(isIdle()).toBe(true)
      
      setVoice({ ...voice, status: 'listening' })
      expect(isIdle()).toBe(false)
    })
  })

  describe('isTtsSpeaking', () => {
    it('returns isTtsSpeaking value', () => {
      setVoice({ ...voice, isTtsSpeaking: true })
      expect(isTtsSpeaking()).toBe(true)
      
      setVoice({ ...voice, isTtsSpeaking: false })
      expect(isTtsSpeaking()).toBe(false)
    })
  })
})