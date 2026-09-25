import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render } from '@solidjs/testing-library'
import { createSignal } from 'solid-js'
import { useVoiceRecording } from './useVoiceRecording'
import { setVoice, setTtsSpeaking } from '../state/voice'

// Mock MediaRecorder and related APIs
const mockMediaRecorder = {
  start: vi.fn(),
  stop: vi.fn(),
  ondataavailable: null as ((e: { data: Blob }) => void) | null,
  onstop: null as (() => void) | null,
  onerror: null as ((e: Error) => void) | null,
  state: 'inactive',
}

const mockStream = {
  getTracks: () => [{ stop: vi.fn() }],
}

const mockAnalyser = {
  fftSize: 256,
  frequencyBinCount: 128,
  getByteFrequencyData: vi.fn(),
  connect: vi.fn(),
}

const mockAudioContext = {
  createAnalyser: vi.fn(() => mockAnalyser),
  createMediaStreamSource: vi.fn(() => ({ connect: vi.fn() })),
  close: vi.fn().mockResolvedValue(undefined),
  currentTime: 0,
}

Object.defineProperty(global, 'AudioContext', {
  value: vi.fn(() => mockAudioContext),
  writable: true,
})

Object.defineProperty(global, 'webkitAudioContext', {
  value: vi.fn(() => mockAudioContext),
  writable: true,
})

Object.defineProperty(global, 'MediaRecorder', {
  value: vi.fn(() => mockMediaRecorder),
  writable: true,
})

Object.defineProperty(MediaRecorder, 'isTypeSupported', {
  value: vi.fn(() => true),
  writable: true,
})

Object.defineProperty(navigator, 'mediaDevices', {
  value: {
    getUserMedia: vi.fn().mockResolvedValue(mockStream),
  },
  writable: true,
})

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  
  // Reset voice state
  setVoice({
    status: 'idle',
    transcript: undefined,
    isDictating: false,
    isTtsSpeaking: false,
  })
  
  // Reset mockMediaRecorder
  mockMediaRecorder.start.mockClear()
  mockMediaRecorder.stop.mockClear()
  mockMediaRecorder.ondataavailable = null
  mockMediaRecorder.onstop = null
  mockMediaRecorder.onerror = null
  mockMediaRecorder.state = 'inactive'
  
  mockAudioContext.createAnalyser.mockClear()
  mockAudioContext.createMediaSource?.mockClear()
  mockAudioContext.close.mockClear()
  
  navigator.mediaDevices.getUserMedia.mockClear()
  
  // Mock analyser data (silent by default)
  mockAnalyser.getByteFrequencyData.mockImplementation((arr: Uint8Array) => {
    arr.fill(0)
  })
})

afterEach(() => {
  vi.useRealTimers()
})

// Test component to access hook internals
function TestComponent(props: {
  isVoiceMode: () => boolean
  isDictationMode: () => boolean
  isTurnActive?: () => boolean
  onTranscription: (text: string) => void
  onReady: (hook: ReturnType<typeof useVoiceRecording>) => void
}) {
  const hook = useVoiceRecording({
    isVoiceMode: props.isVoiceMode,
    isDictationMode: props.isDictationMode,
    isTurnActive: props.isTurnActive ?? (() => false),
    onTranscription: props.onTranscription,
  })
  
  props.onReady(hook)
  
  return null
}

describe('useVoiceRecording hook', () => {
  describe('TTS pause/resume coordination', () => {
    it('pauses recording when TTS starts speaking', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      
      render(() => (
        <TestComponent
          isVoiceMode={() => true}
          isDictationMode={() => false}
          onTranscription={vi.fn()}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      // Wait for auto-start (100ms delay)
      await vi.advanceTimersByTimeAsync(150)
      
      expect(hookRef?.isRecording()).toBe(true)
      expect(mockMediaRecorder.start).toHaveBeenCalled()
      
      // Start TTS
      setTtsSpeaking(true)
      
      // Recording should stop
      expect(hookRef?.isRecording()).toBe(false)
      expect(mockMediaRecorder.stop).toHaveBeenCalled()
    })
    
    it('does NOT resume listening after TTS ends if turn is active', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      
      render(() => (
        <TestComponent
          isVoiceMode={() => true}
          isDictationMode={() => false}
          isTurnActive={() => true} // Turn is active!
          onTranscription={vi.fn()}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      await vi.advanceTimersByTimeAsync(150)
      
      // Should NOT auto-start because turn is active
      expect(hookRef?.isRecording()).toBe(false)
      expect(mockMediaRecorder.start).not.toHaveBeenCalled()
      
      // Start and end TTS
      setTtsSpeaking(true)
      await vi.runAllTimersAsync()
      setTtsSpeaking(false)
      await vi.advanceTimersByTimeAsync(150)
      
      // Still should not record
      expect(hookRef?.isRecording()).toBe(false)
      expect(mockMediaRecorder.start).not.toHaveBeenCalled()
    })
  })
  
  describe('Turn active coordination', () => {
    it('stops recording when turn becomes active', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      const [turnActive, setTurnActive] = createSignal(false)
      
      render(() => (
        <TestComponent
          isVoiceMode={() => true}
          isDictationMode={() => false}
          isTurnActive={turnActive}
          onTranscription={vi.fn()}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      await vi.advanceTimersByTimeAsync(150)
      
      expect(hookRef?.isRecording()).toBe(true)
      
      // Turn becomes active (e.g., user message sent)
      setTurnActive(true)
      
      // Recording should stop
      expect(hookRef?.isRecording()).toBe(false)
      expect(mockMediaRecorder.stop).toHaveBeenCalled()
    })
    
    it('does not auto-start recording when turn is active, but starts when turn ends', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      const [turnActive, setTurnActive] = createSignal(true)
      
      render(() => (
        <TestComponent
          isVoiceMode={() => true}
          isDictationMode={() => false}
          isTurnActive={turnActive}
          onTranscription={vi.fn()}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      await vi.advanceTimersByTimeAsync(150)
      
      // Should not start because turn is active
      expect(hookRef?.isRecording()).toBe(false)
      expect(mockMediaRecorder.start).not.toHaveBeenCalled()
      
      // Turn ends
      setTurnActive(false)
      await vi.advanceTimersByTimeAsync(150)
      
      // Now should start
      expect(hookRef?.isRecording()).toBe(true)
      expect(mockMediaRecorder.start).toHaveBeenCalled()
    })
  })
  
  describe('Voice mode activation/deactivation', () => {
    it('starts recording when voice mode activates (after 100ms delay)', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      const [voiceMode, setVoiceMode] = createSignal(false)
      
      render(() => (
        <TestComponent
          isVoiceMode={voiceMode}
          isDictationMode={() => false}
          onTranscription={vi.fn()}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      await vi.advanceTimersByTimeAsync(150)
      expect(hookRef?.isRecording()).toBe(false)
      
      setVoiceMode(true)
      await vi.advanceTimersByTimeAsync(150)
      
      expect(hookRef?.isRecording()).toBe(true)
      expect(mockMediaRecorder.start).toHaveBeenCalled()
    })
    
    it('stops recording when voice mode deactivates', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      const [voiceMode, setVoiceMode] = createSignal(true)
      
      render(() => (
        <TestComponent
          isVoiceMode={voiceMode}
          isDictationMode={() => false}
          onTranscription={vi.fn()}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      await vi.advanceTimersByTimeAsync(150)
      expect(hookRef?.isRecording()).toBe(true)
      
      setVoiceMode(false)
      
      expect(hookRef?.isRecording()).toBe(false)
      expect(mockMediaRecorder.stop).toHaveBeenCalled()
    })
  })
  
  describe('Cleanup', () => {
    it('cleans up on unmount', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      
      const { unmount } = render(() => (
        <TestComponent
          isVoiceMode={() => true}
          isDictationMode={() => false}
          onTranscription={vi.fn()}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      await vi.advanceTimersByTimeAsync(150)
      expect(hookRef?.isRecording()).toBe(true)
      
      unmount()
      
      // Should stop recording and clean up
      expect(mockMediaRecorder.stop).toHaveBeenCalled()
      expect(mockAudioContext.close).toHaveBeenCalled()
    })
  })

  describe('Transcription callback', () => {
    it('calls onTranscription after successful transcription in voice mode (regression test for bug where startProcessing() changed voice status before callback)', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      const onTranscription = vi.fn()
      
      render(() => (
        <TestComponent
          isVoiceMode={() => true}
          isDictationMode={() => false}
          onTranscription={onTranscription}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      // Wait for auto-start
      await vi.advanceTimersByTimeAsync(150)
      expect(hookRef?.isRecording()).toBe(true)
      expect(mockMediaRecorder.start).toHaveBeenCalled()
      
      // Simulate loud audio to pass the audio level checks
      mockAnalyser.getByteFrequencyData.mockImplementation((arr: Uint8Array) => {
        arr.fill(200) // Loud audio
      })
      
      // Advance time to pass MIN_RECORDING_MS (500ms)
      await vi.advanceTimersByTimeAsync(600)
      
      // Simulate audio data being recorded by calling ondataavailable
      const mockBlob = new Blob(['test audio'], { type: 'audio/ogg' })
      if (mockMediaRecorder.ondataavailable) {
        mockMediaRecorder.ondataavailable({ data: mockBlob })
      }
      
      // Mock fetch to return successful transcription
      const originalFetch = global.fetch
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ text: 'Hello world' }),
      })
      
      // Trigger onstop by calling stop on the MediaRecorder mock
      // This simulates stopRecording() being called
      if (mockMediaRecorder.onstop) {
        mockMediaRecorder.onstop()
      }
      
      // Wait for async transcription to complete
      await vi.waitFor(() => {
        expect(onTranscription).toHaveBeenCalledWith('Hello world')
      }, { timeout: 15000 })
      
      // Restore fetch
      global.fetch = originalFetch
    }, 20000)
    
    it('calls onTranscription after successful transcription in dictation mode', async () => {
      let hookRef: ReturnType<typeof useVoiceRecording> | null = null
      const onTranscription = vi.fn()
      let voiceMode = true // Start with voice mode true to avoid "voice mode deactivated" effect
      let dictationMode = false
      
      render(() => (
        <TestComponent
          isVoiceMode={() => voiceMode}
          isDictationMode={() => dictationMode}
          onTranscription={onTranscription}
          onReady={(h) => { hookRef = h }}
        />
      ))
      
      // Wait for auto-start in voice mode
      await vi.advanceTimersByTimeAsync(150)
      expect(hookRef?.isRecording()).toBe(true)
      
      // Switch to dictation mode (voice mode off, dictation on)
      voiceMode = false
      dictationMode = true
      
      // Manually restart recording for dictation (since voice mode off would stop it)
      hookRef?.stopRecording()
      await vi.advanceTimersByTimeAsync(50)
      hookRef?.startRecording()
      
      // Simulate loud audio to pass the audio level checks
      mockAnalyser.getByteFrequencyData.mockImplementation((arr: Uint8Array) => {
        arr.fill(200)
      })
      
      // Advance time to pass MIN_RECORDING_MS (500ms)
      await vi.advanceTimersByTimeAsync(800)
      
      // Simulate audio data being recorded
      const mockBlob = new Blob(['test audio'], { type: 'audio/ogg' })
      if (mockMediaRecorder.ondataavailable) {
        mockMediaRecorder.ondataavailable({ data: mockBlob })
      }
      
      // Mock fetch to return successful transcription
      const originalFetch = global.fetch
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ text: 'Dictation test' }),
      })
      
      // Trigger onstop
      if (mockMediaRecorder.onstop) {
        mockMediaRecorder.onstop()
      }
      
      // Wait for async transcription to complete
      await vi.waitFor(() => {
        expect(onTranscription).toHaveBeenCalledWith('Dictation test')
      }, { timeout: 15000 })
      
      // Restore fetch
      global.fetch = originalFetch
    }, 20000)
  })
})