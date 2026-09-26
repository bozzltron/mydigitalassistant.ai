import { createSignal, createEffect, onCleanup } from 'solid-js'
import { 
  endDictation, 
  startListening, 
  startProcessing,
  exitVoiceMode,
  registerStopRecording,
  unregisterStopRecording,
  isTtsSpeaking
} from '../state/voice'
import { settings } from '../state/settings'
import { VoiceActivityDetector } from '../services/voiceActivity'

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

const SILENCE_DURATION = 2000
const MIN_RECORDING_MS = 500
const MIN_AUDIO_FRAMES = 3
const MAX_RECORDING_MS = 180000
const MONITOR_INTERVAL_MS = 80

type VoiceModeState = 'idle' | 'starting' | 'recording' | 'processing' | 'paused_tts' | 'user_stopped'

function playEarcon(type: 'start' | 'stop' | 'error') {
  // Check if sound effects are enabled
  if (!settings.soundEffectsEnabled) return
  
  if (!window.AudioContext && !window.webkitAudioContext) return
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain)
    gain.connect(ctx.destination)
    const now = ctx.currentTime
    if (type === 'start') {
      osc.type = 'sine'
      osc.frequency.setValueAtTime(880, now)
      osc.frequency.exponentialRampToValueAtTime(1760, now + 0.08)
      gain.gain.setValueAtTime(0.08, now)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12)
      osc.start(now)
      osc.stop(now + 0.12)
    } else if (type === 'stop') {
      osc.type = 'sine'
      osc.frequency.setValueAtTime(1760, now)
      osc.frequency.exponentialRampToValueAtTime(880, now + 0.08)
      gain.gain.setValueAtTime(0.08, now)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12)
      osc.start(now)
      osc.stop(now + 0.12)
    } else if (type === 'error') {
      osc.type = 'triangle'
      osc.frequency.setValueAtTime(300, now)
      gain.gain.setValueAtTime(0.06, now)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25)
      osc.start(now)
      osc.stop(now + 0.25)
    }
    setTimeout(() => ctx.close(), 300)
  } catch {
    // ignore
  }
}

export function useVoiceRecording({
  isVoiceMode,
  isDictationMode,
  isTurnActive = () => false,
  onTranscription,
}: UseVoiceRecordingOptions): UseVoiceRecordingReturn {
  const getIsVoiceMode = isVoiceMode
  const getIsDictationMode = isDictationMode
  const getIsTurnActive = isTurnActive
  
  console.log('[useVoiceRecording] init')
  
  // Core recording state
  const [mediaRecorder, setMediaRecorder] = createSignal<MediaRecorder | null>(null)
  const [audioChunks, setAudioChunks] = createSignal<Blob[]>([])
  const [isRecording, setIsRecording] = createSignal(false)
  const [currentMimeType, setCurrentMimeType] = createSignal<string | null>(null)
  const [audioContext, setAudioContext] = createSignal<AudioContext | null>(null)
  const [analyser, setAnalyser] = createSignal<AnalyserNode | null>(null)
  const [mediaStream, setMediaStream] = createSignal<MediaStream | null>(null)
  const [silenceTimeout, setSilenceTimeout] = createSignal<number | null>(null)
  const [recordingTimeoutId, setRecordingTimeoutId] = createSignal<number | null>(null)
  const [recordingStartTime, setRecordingStartTime] = createSignal<number>(0)
  const [loudFrameCount, setLoudFrameCount] = createSignal(0)
  const [silenceAfterLoud, setSilenceAfterLoud] = createSignal(false)
  const [monitorIntervalId, setMonitorIntervalId] = createSignal<number | null>(null)
  
  // Owns the silence decision and the adaptive noise floor. Reset at the start
  // of every recording so one turn's calibration never leaks into the next.
  const vad = new VoiceActivityDetector()

  // State machine state
  const [modeState, setModeState] = createSignal<VoiceModeState>('idle')
  const [userInitiatedStop, setUserInitiatedStop] = createSignal(false)
  
  // Track timers for cleanup
  const [pendingTimeouts, setPendingTimeouts] = createSignal<Set<number>>(new Set())
  
  function addTimeout(id: number) {
    setPendingTimeouts(prev => new Set(prev).add(id))
  }


  // Register stopRecording callback for external access (e.g., TopBar buttons)
  onCleanup(() => {
    unregisterStopRecording()
  })
  
  const stopRecordingFn = () => {
    stopRecording()
  }
  
  createEffect(() => {
    registerStopRecording(stopRecordingFn)
  })

  // Unified state machine - single effect managing all transitions
  createEffect(() => {
    const voiceMode = getIsVoiceMode()
    const turnActive = getIsTurnActive()
    const ttsSpeaking = isTtsSpeaking()
    const state = modeState()
    const recording = isRecording()

    console.log('[useVoiceRecording] state machine tick', { state, voiceMode, turnActive, ttsSpeaking, recording, userStop: userInitiatedStop() })

    switch (state) {
      case 'idle':
        if (voiceMode && !turnActive && !ttsSpeaking && !userInitiatedStop()) {
          console.log('[useVoiceRecording] idle -> starting')
          setModeState('starting')
          startRecording()
        }
        break
        
      case 'starting':
        // Waiting for startRecording to complete (sets isRecording=true)
        if (!voiceMode || turnActive || ttsSpeaking) {
          console.log('[useVoiceRecording] starting -> idle (condition lost)')
          setModeState('idle')
          if (recording) stopRecording()
        }
        break
        
      case 'recording':
        if (!voiceMode || turnActive) {
          console.log('[useVoiceRecording] recording -> idle (voiceMode/turnActive lost)')
          setModeState('idle')
          stopRecording()
        } else if (ttsSpeaking) {
          console.log('[useVoiceRecording] recording -> paused_tts (TTS started)')
          setModeState('paused_tts')
          stopRecording()
        } else if (userInitiatedStop()) {
          console.log('[useVoiceRecording] recording -> user_stopped (user clicked stop)')
          setModeState('user_stopped')
          stopRecording()
          setUserInitiatedStop(false)
        }
        break
        
      case 'processing':
        // Transcription in progress
        if (!voiceMode) {
          console.log('[useVoiceRecording] processing -> idle (voiceMode lost)')
          setModeState('idle')
        } else if (!turnActive && !ttsSpeaking && !userInitiatedStop()) {
          // Transcription done, ready to resume listening
          console.log('[useVoiceRecording] processing -> starting (turn complete, resuming)')
          setModeState('starting')
        } else if (turnActive) {
          // Turn became active, wait for it to complete
          console.log('[useVoiceRecording] processing -> waiting for turn')
        }
        break
        
      case 'paused_tts':
        if (!voiceMode || turnActive) {
          console.log('[useVoiceRecording] paused_tts -> idle (voiceMode/turnActive lost)')
          setModeState('idle')
        } else if (!ttsSpeaking && !userInitiatedStop()) {
          console.log('[useVoiceRecording] paused_tts -> starting (TTS ended)')
          setModeState('starting')
        } else if (userInitiatedStop()) {
          console.log('[useVoiceRecording] paused_tts -> user_stopped (user clicked stop during TTS)')
          setModeState('user_stopped')
          setUserInitiatedStop(false)
        }
        break
        
      case 'user_stopped':
        // Stay stopped until voice mode is exited
        if (!voiceMode) {
          console.log('[useVoiceRecording] user_stopped -> idle (voiceMode exited)')
          setModeState('idle')
        }
        break
    }
  })

  function checkAudioLevels() {
    const reading = vad.read(analyser())
    if (!reading) return
    const elapsed = Date.now() - recordingStartTime()
    const metMinDuration = elapsed >= MIN_RECORDING_MS
    if (reading.speech) {
      if (loudFrameCount() === 0) {
        console.log('[voice] speech detected', { rms: reading.rms, threshold: reading.threshold })
      }
      setLoudFrameCount(loudFrameCount() + 1)
      setSilenceAfterLoud(false)
      const st = silenceTimeout()
      if (st) {
        clearTimeout(st)
        setSilenceTimeout(null)
      }
    } else {
      if (!silenceAfterLoud() && loudFrameCount() > 0) {
        setSilenceAfterLoud(true)
        console.log('[voice] silence detected after speech, will timeout in', SILENCE_DURATION, 'ms')
      }
      if (!silenceTimeout() && metMinDuration && loudFrameCount() >= MIN_AUDIO_FRAMES) {
        console.log('[voice] setting silence timeout:', SILENCE_DURATION, 'ms')
        const st = window.setTimeout(() => {
          if (isRecording()) {
            console.log('[voice] silence timeout fired — stopping')
            stopRecording()
          }
        }, SILENCE_DURATION)
        setSilenceTimeout(st)
        addTimeout(st)
      }
    }
  }

  function startAudioMonitor() {
    stopAudioMonitor()
    const id = window.setInterval(checkAudioLevels, MONITOR_INTERVAL_MS)
    setMonitorIntervalId(id)
  }

  function stopAudioMonitor() {
    const id = monitorIntervalId()
    if (id !== null) {
      clearInterval(id)
      setMonitorIntervalId(null)
    }
  }

  async function startRecording() {
    console.log('[voice] startRecording called', { isRecording: isRecording(), modeState: modeState() })
    if (isRecording()) return
    try {
      const ctx = audioContext()
      if (ctx) {
        await ctx.close().catch(() => {})
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      console.log('[voice] got media stream', stream.getTracks())

      setMediaStream(stream)

      const newAudioContext = new (window.AudioContext || window.webkitAudioContext)()
      setAudioContext(newAudioContext)
      
      const newAnalyser = newAudioContext.createAnalyser()
      newAnalyser.fftSize = 256
      setAnalyser(newAnalyser)
      
      const source = newAudioContext.createMediaStreamSource(stream)
      source.connect(newAnalyser)

      const mimeType =
        MediaRecorder.isTypeSupported('audio/ogg') ? 'audio/ogg' :
        MediaRecorder.isTypeSupported('audio/wav') ? 'audio/wav' :
        MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' :
        MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' :
        null
      if (!mimeType) {
        console.error('Audio recording not supported in this browser')
        if (getIsVoiceMode()) {
          startProcessing()
          setTimeout(() => exitVoiceMode(), 100)
        }
        stream.getTracks().forEach(t => t.stop())
        return
      }
      console.log('[voice] selected mimeType:', mimeType)
      const mr = new MediaRecorder(stream, { mimeType })
      setMediaRecorder(mr)
      setAudioChunks([])
      setCurrentMimeType(mimeType)

      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          setAudioChunks(prev => [...prev, e.data])
          console.log('[voice] chunk received:', e.data.size, 'total chunks:', audioChunks().length + 1, 'mime:', mimeType)
        } else {
          console.log('[voice] empty chunk received')
        }
      }

      mr.onstop = async () => {
        const elapsed = Date.now() - recordingStartTime()
        const currentLoudFrames = loudFrameCount()
        // Capture voice mode state at stop time (before any state changes)
        const wasVoiceModeAtStop = getIsVoiceMode()
        console.log('[voice] onstop chunks:', audioChunks().length, 'elapsed:', elapsed, 'loudFrames:', currentLoudFrames)
        try {
          if (audioChunks().length === 0) {
            handleRecordingDiscard("Didn't catch that", wasVoiceModeAtStop)
            return
          }
          const tooShort = elapsed < MIN_RECORDING_MS
          const notEnoughLoud = currentLoudFrames < MIN_AUDIO_FRAMES
          console.log('[voice] onstop checks tooShort:', tooShort, 'notEnoughLoud:', notEnoughLoud)
          if (tooShort || notEnoughLoud) {
            handleRecordingDiscard("Didn't catch that", wasVoiceModeAtStop)
            return
          }
          const blob = new Blob(audioChunks(), { type: currentMimeType() || 'audio/webm' })
          setAudioChunks([])
          console.log('[voice] sending blob size:', blob.size, 'mime:', currentMimeType())
          await sendAudioForTranscription(blob)
        } catch (err) {
          console.error('[voice] onstop error:', err)
        }
      }

      mr.onerror = (e) => {
        console.error('MediaRecorder error:', e)
        // Capture voice mode state at error time
        const wasVoiceModeAtError = getIsVoiceMode()
        if (wasVoiceModeAtError) {
          scheduleListenRetry('Recording error, retrying...')
        }
      }

      mr.start()
      setIsRecording(true)
      setRecordingStartTime(Date.now())
      setLoudFrameCount(0)
      setSilenceAfterLoud(false)
      vad.reset()

      startAudioMonitor()

      const timeoutId = window.setTimeout(() => {
        if (isRecording()) stopRecording()
      }, MAX_RECORDING_MS)
      setRecordingTimeoutId(timeoutId)
      addTimeout(timeoutId)
    } catch (e) {
      console.warn('[voice] Failed to start recording:', e)
      if (getIsVoiceMode()) {
        startProcessing()
        setTimeout(() => exitVoiceMode(), 100)
      }
    }
  }

  function stopRecording() {
    if (!isRecording() || !mediaRecorder()) return
    const st = silenceTimeout()
    if (st) {
      clearTimeout(st)
      setSilenceTimeout(null)
    }
    const rt = recordingTimeoutId()
    if (rt) {
      clearTimeout(rt)
      setRecordingTimeoutId(null)
    }
    stopAudioMonitor()
    const mr = mediaRecorder()
    setMediaRecorder(null)
    setIsRecording(false)
    setSilenceAfterLoud(false)
    try {
      mr.stop()
    } catch (e) {
      console.warn('Error stopping recorder:', e)
    }
    const ms = mediaStream()
    if (ms) {
      ms.getTracks().forEach(t => t.stop())
      setMediaStream(null)
    }
    const ctx = audioContext()
    if (ctx) {
      ctx.close().catch(() => {})
      setAudioContext(null)
      setAnalyser(null)
    }
  }

  function handleRecordingDiscard(message: string, voiceModeOverride?: boolean) {
    stopRecording()
    const voiceMode = voiceModeOverride ?? getIsVoiceMode()
    const dictationMode = getIsDictationMode()
    if (dictationMode) {
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

  async function sendAudioForTranscription(blob: Blob) {
    console.log('[voice] sendAudioForTranscription blob:', blob.size, 'mime:', blob.type, 'voiceMode:', getIsVoiceMode(), 'dictation:', getIsDictationMode())
    // Capture voice mode state BEFORE startProcessing() changes it to 'processing'
    const wasVoiceMode = getIsVoiceMode()
    const wasDictationMode = getIsDictationMode()
    if (!wasVoiceMode && !wasDictationMode) return
    startProcessing()
    // State machine will transition to 'processing'
    playEarcon('stop')

    try {
      const mimeExt = (currentMimeType() || 'audio/webm').split('/')[1]
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
      console.log('[voice] transcription response:', text)

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
        console.error('Transcription failed')
      } else {
        console.error('Transcription failed')
        scheduleListenRetry('Transcription error, retrying...')
      }
    }
  }

  // Cleanup all pending timeouts on unmount
  onCleanup(() => {
    stopRecording()
    stopAudioMonitor()
    // Clear any pending timeouts
    pendingTimeouts().forEach(id => clearTimeout(id))
  })

  // Public stopRecording that sets userInitiatedStop flag
  const handleUserStop = () => {
    console.log('[useVoiceRecording] User initiated stop')
    setUserInitiatedStop(true)
    stopRecording()
  }

  return {
    isRecording,
    startRecording,
    stopRecording: handleUserStop,
  }
}

const EXIT_COMMANDS = ['stop listening', 'exit voice mode', 'stop voice mode', 'goodbye', 'bye']

function isExitCommand(text: string): boolean {
  const t = text.toLowerCase().trim()
  return EXIT_COMMANDS.includes(t)
}