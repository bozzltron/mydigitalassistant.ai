import { createSignal, createEffect, onCleanup } from 'solid-js'
import { debug } from '../services/logger'
import {
  endDictation,
  startListening,
  startProcessing,
  exitVoiceMode,
  registerStopRecording,
  unregisterStopRecording,
  isTtsSpeaking,
} from '../state/voice'
import { isExitCommand, playEarcon } from '../services/audio'
import { createVoiceCapture } from './voiceCapture'

interface UseVoiceRecordingOptions {
  isVoiceMode: () => boolean
  isDictationMode: () => boolean
  isTurnActive?: () => boolean
  onTranscription: (text: string) => void
}

interface UseVoiceRecordingReturn {
  isRecording: () => boolean
  startRecording: () => void
  stopRecording: () => void
}

type VoiceModeState = 'idle' | 'starting' | 'recording' | 'processing' | 'paused_tts' | 'user_stopped'

export function useVoiceRecording({
  isVoiceMode,
  isDictationMode,
  isTurnActive = () => false,
  onTranscription,
}: UseVoiceRecordingOptions): UseVoiceRecordingReturn {
  const getIsVoiceMode = isVoiceMode
  const getIsDictationMode = isDictationMode
  const getIsTurnActive = isTurnActive

  // State machine state
  const [modeState, setModeState] = createSignal<VoiceModeState>('idle')
  const [userInitiatedStop, setUserInitiatedStop] = createSignal(false)

  // Track timers for cleanup (scheduleListenRetry's no-op timeout).
  const [pendingTimeouts, setPendingTimeouts] = createSignal<Set<number>>(new Set())

  function addTimeout(id: number) {
    setPendingTimeouts(prev => new Set(prev).add(id))
  }

  const capture = createVoiceCapture({
    label: '[voice]',
    // Either mode counts: dictation records with voice mode off by design, so
    // asking only about voice mode would drop every dictation capture.
    shouldCapture: () => getIsVoiceMode() || getIsDictationMode(),
    onCapture: (blob, mime) => sendAudioForTranscription(blob, mime),
    onDiscard: (reason) => handleRecordingDiscard(reason),
    onStartError: () => {
      if (getIsVoiceMode()) {
        startProcessing()
        setTimeout(() => exitVoiceMode(), 100)
      }
    },
    onRecorderError: () => {
      if (getIsVoiceMode()) {
        scheduleListenRetry('Recording error, retrying...')
      }
    },
    // Back to 'starting -> idle' is a dead end: the machine only leaves
    // 'starting' when a condition is lost, and by now the condition that caused
    // the drop may already be gone. Nudging the state re-enters 'idle', which
    // re-checks everything.
    onStartDropped: () => setModeState('idle'),
  })

  const isRecording = capture.isRecording

  // Register stopRecording callback for external access (e.g., TopBar buttons)
  createEffect(() => {
    registerStopRecording(() => capture.stop())
  })

  onCleanup(() => {
    unregisterStopRecording()
  })

  // Unified state machine - single effect managing all transitions
  createEffect(() => {
    const voiceMode = getIsVoiceMode()
    const turnActive = getIsTurnActive()
    const ttsSpeaking = isTtsSpeaking()
    const state = modeState()
    const recording = capture.isRecording()

    debug('[useVoiceRecording] state machine tick', { state, voiceMode, turnActive, ttsSpeaking, recording, userStop: userInitiatedStop() })

    switch (state) {
      case 'idle':
        if (voiceMode && !turnActive && !ttsSpeaking && !userInitiatedStop()) {
          debug('[useVoiceRecording] idle -> starting')
          setModeState('starting')
          void capture.start()
        }
        break

      case 'starting':
        // Waiting for start() to complete (sets isRecording=true)
        if (!voiceMode || turnActive || ttsSpeaking) {
          debug('[useVoiceRecording] starting -> idle (condition lost)')
          setModeState('idle')
          if (recording) capture.stop()
        }
        break

      case 'recording':
        if (!voiceMode || turnActive) {
          debug('[useVoiceRecording] recording -> idle (voiceMode/turnActive lost)')
          setModeState('idle')
          capture.stop()
        } else if (ttsSpeaking) {
          debug('[useVoiceRecording] recording -> paused_tts (TTS started)')
          setModeState('paused_tts')
          capture.stop()
        } else if (userInitiatedStop()) {
          debug('[useVoiceRecording] recording -> user_stopped (user clicked stop)')
          setModeState('user_stopped')
          capture.stop()
          setUserInitiatedStop(false)
        }
        break

      case 'processing':
        // Transcription in progress
        if (!voiceMode) {
          debug('[useVoiceRecording] processing -> idle (voiceMode lost)')
          setModeState('idle')
        } else if (!turnActive && !ttsSpeaking && !userInitiatedStop()) {
          // Transcription done, ready to resume listening
          debug('[useVoiceRecording] processing -> starting (turn complete, resuming)')
          setModeState('starting')
        } else if (turnActive) {
          // Turn became active, wait for it to complete
          debug('[useVoiceRecording] processing -> waiting for turn')
        }
        break

      case 'paused_tts':
        if (!voiceMode || turnActive) {
          debug('[useVoiceRecording] paused_tts -> idle (voiceMode/turnActive lost)')
          setModeState('idle')
        } else if (!ttsSpeaking && !userInitiatedStop()) {
          debug('[useVoiceRecording] paused_tts -> starting (TTS ended)')
          setModeState('starting')
        } else if (userInitiatedStop()) {
          debug('[useVoiceRecording] paused_tts -> user_stopped (user clicked stop during TTS)')
          setModeState('user_stopped')
          setUserInitiatedStop(false)
        }
        break

      case 'user_stopped':
        // Stay stopped until voice mode is exited
        if (!voiceMode) {
          debug('[useVoiceRecording] user_stopped -> idle (voiceMode exited)')
          setModeState('idle')
        }
        break
    }
  })

  function handleRecordingDiscard(message: string, voiceModeOverride?: boolean) {
    // The capture is already stopped when this runs from the core; only the
    // policy side is left to decide.
    const voiceMode = voiceModeOverride ?? getIsVoiceMode()
    if (getIsDictationMode()) {
      endDictation()
    } else if (voiceMode) {
      scheduleListenRetry(message)
    }
  }

  function scheduleListenRetry(_message: string, delayMs = 1200) {
    startProcessing()
    // State machine will handle transition to 'processing' and back to 'starting'
    const timeoutId = window.setTimeout(() => {
      // The state machine effect will handle the restart when conditions are met
    }, delayMs)
    addTimeout(timeoutId)
  }

  async function sendAudioForTranscription(blob: Blob, mime: string) {
    // Capture voice/dictation state BEFORE startProcessing() changes status
    const wasVoiceMode = getIsVoiceMode()
    const wasDictationMode = getIsDictationMode()
    if (!wasVoiceMode && !wasDictationMode) return
    startProcessing()
    playEarcon('stop')

    try {
      const mimeExt = (mime || 'audio/webm').split('/')[1]
      const formData = new FormData()
      formData.append('file', blob, `audio.${mimeExt}`)

      const res = await fetch('/transcribe', {
        method: 'POST',
        body: formData,
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Transcription failed' }))
        throw new Error(err.detail || 'Transcription failed')
      }

      const data = await res.json()
      const text = (data.text || '').trim()
      debug('[voice] transcription response:', text)

      if (!text) {
        handleRecordingDiscard("Didn't catch that", wasVoiceMode)
        return
      }

      if (wasDictationMode) {
        endDictation()
        onTranscription(text)
        return
      }

      if (isExitCommand(text)) {
        exitVoiceMode()
        return
      }

      if (wasVoiceMode) {
        // State machine will handle transition back to 'starting' after turn completes
        // Set status to 'listening' so UI shows listening state
        startListening()
        onTranscription(text)
      }
    } catch (err) {
      console.error('Transcription error:', err)
      if (wasDictationMode) {
        endDictation()
      } else {
        scheduleListenRetry('Transcription error, retrying...')
      }
    }
  }

  // Cleanup all pending timeouts on unmount
  onCleanup(() => {
    capture.cleanup()
    pendingTimeouts().forEach(id => clearTimeout(id))
  })

  // Public stopRecording that sets userInitiatedStop flag
  const handleUserStop = () => {
    debug('[useVoiceRecording] User initiated stop')
    setUserInitiatedStop(true)
    capture.stop()
  }

  return {
    isRecording,
    startRecording: capture.start,
    stopRecording: handleUserStop,
  }
}
