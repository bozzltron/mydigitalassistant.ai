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

interface UseVoiceRecordingOptions {
  isVoiceMode: boolean
  isDictationMode: boolean
  onTranscription: (text: string) => void
}

interface UseVoiceRecordingReturn {
  isRecording: () => boolean
  startRecording: () => void
  stopRecording: () => void
}

const SILENCE_DURATION = 2000
const MIN_RECORDING_MS = 500
const MIN_AUDIO_LEVEL = 0.015
const MIN_AUDIO_FRAMES = 3
const MAX_RECORDING_MS = 180000
const MONITOR_INTERVAL_MS = 80

function playEarcon(type: 'start' | 'stop' | 'error') {
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
  onTranscription,
}: UseVoiceRecordingOptions): UseVoiceRecordingReturn {
  // Call getters if they are functions
  const getIsVoiceMode = typeof isVoiceMode === 'function' ? isVoiceMode : () => isVoiceMode
  const getIsDictationMode = typeof isDictationMode === 'function' ? isDictationMode : () => isDictationMode
  
  console.log('[useVoiceRecording] init')
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

  // Pause/resume recording during TTS playback
  createEffect(() => {
    const ttsSpeaking = isTtsSpeaking()
    const voiceMode = getIsVoiceMode()
    
    if (ttsSpeaking && isRecording()) {
      console.log('[useVoiceRecording] TTS started — pausing recording')
      stopRecording()
    } else if (!ttsSpeaking && voiceMode && !isRecording()) {
      console.log('[useVoiceRecording] TTS ended — resuming listening')
      // Small delay to ensure TTS has fully stopped
      setTimeout(() => {
        if (!isTtsSpeaking() && getIsVoiceMode() && !isRecording()) {
          startListeningForVoice()
        }
      }, 100)
    }
  })

  // Auto-start recording when voice mode is activated
  createEffect(() => {
    const voiceMode = getIsVoiceMode()
    console.log('[useVoiceRecording] createEffect check', { voiceMode, isRecording: isRecording() })
    if (voiceMode && !isRecording()) {
      // Small delay to ensure UI is ready
      setTimeout(() => {
        console.log('[useVoiceRecording] timeout check', { voiceMode: getIsVoiceMode(), isRecording: isRecording() })
        if (getIsVoiceMode() && !isRecording()) {
          console.log('[useVoiceRecording] auto-starting recording')
          startRecording()
        }
      }, 100)
    }
  })

  // Stop recording when voice mode is deactivated
  createEffect(() => {
    const voiceMode = getIsVoiceMode()
    if (!voiceMode && isRecording()) {
      console.log('[useVoiceRecording] voice mode deactivated — stopping recording')
      stopRecording()
    }
  })

  function checkAudioLevels() {
    const a = analyser()
    if (!a) return
    const dataArray = new Uint8Array(a.frequencyBinCount)
    a.getByteFrequencyData(dataArray)
    let sum = 0
    for (let i = 0; i < dataArray.length; i++) {
      sum += dataArray[i]
    }
    const average = sum / dataArray.length / 255
    const elapsed = Date.now() - recordingStartTime()
    const metMinDuration = elapsed >= MIN_RECORDING_MS
    const hasLoudAudio = average >= MIN_AUDIO_LEVEL
    if (hasLoudAudio) {
      setLoudFrameCount(loudFrameCount() + 1)
      setSilenceAfterLoud(false)
      const st = silenceTimeout()
      if (st) {
        clearTimeout(st)
        setSilenceTimeout(null)
      }
      console.log('[voice] loud audio', { average, loudFrames: loudFrameCount(), elapsed, metMinDuration })
    } else {
      if (!silenceAfterLoud() && loudFrameCount() > 0) {
        setSilenceAfterLoud(true)
        console.log('[voice] silence detected after loud audio, will timeout in', SILENCE_DURATION, 'ms')
      }
      if (!silenceTimeout() && metMinDuration && loudFrameCount() >= MIN_AUDIO_FRAMES) {
        console.log('[voice] setting silence timeout:', SILENCE_DURATION, 'ms, loudFrames:', loudFrameCount())
        const st = window.setTimeout(() => {
          if (isRecording()) {
            console.log('[voice] silence timeout fired — stopping')
            stopRecording()
          }
        }, SILENCE_DURATION)
        setSilenceTimeout(st)
      } else if (loudFrameCount() > 0) {
        console.log('[voice] waiting for silence timeout conditions', { 
          metMinDuration, 
          loudFrames: loudFrameCount(), 
          minFrames: MIN_AUDIO_FRAMES,
          hasTimeout: !!silenceTimeout() 
        })
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
    console.log('[voice] startRecording called', { isRecording: isRecording() })
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
        console.log('[voice] onstop chunks:', audioChunks().length, 'elapsed:', elapsed, 'loudFrames:', currentLoudFrames)
        try {
          if (audioChunks().length === 0) {
            handleRecordingDiscard("Didn't catch that")
            return
          }
          const tooShort = elapsed < MIN_RECORDING_MS
          const notEnoughLoud = currentLoudFrames < MIN_AUDIO_FRAMES
          console.log('[voice] onstop checks tooShort:', tooShort, 'notEnoughLoud:', notEnoughLoud)
          if (tooShort || notEnoughLoud) {
            handleRecordingDiscard("Didn't catch that")
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
        if (getIsVoiceMode()) {
          scheduleListenRetry('Recording error, retrying...')
        }
      }

      mr.start()
      setIsRecording(true)
      setRecordingStartTime(Date.now())
      setLoudFrameCount(0)
      setSilenceAfterLoud(false)

      startAudioMonitor()

      const timeoutId = window.setTimeout(() => {
        if (isRecording()) stopRecording()
      }, MAX_RECORDING_MS)
      setRecordingTimeoutId(timeoutId)
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

  function handleRecordingDiscard(message: string) {
    stopRecording()
    if (getIsDictationMode()) {
      endDictation()
    } else if (getIsVoiceMode()) {
      scheduleListenRetry(message)
    }
  }

  function scheduleListenRetry(message: string, delayMs = 1200) {
    startProcessing()
    window.setTimeout(() => {
      if (getIsVoiceMode()) {
        startListeningForVoice()
      }
    }, delayMs)
  }

  function startListeningForVoice() {
    if (!getIsVoiceMode()) return
    startListening()
    playEarcon('start')
    startRecording()
  }

  async function sendAudioForTranscription(blob: Blob) {
    console.log('[voice] sendAudioForTranscription blob:', blob.size, 'mime:', blob.type, 'voiceMode:', getIsVoiceMode(), 'dictation:', getIsDictationMode())
    if (!getIsVoiceMode() && !getIsDictationMode()) return
    startProcessing()
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
        handleRecordingDiscard("Didn't catch that")
        return
      }

      if (getIsDictationMode()) {
        endDictation()
        onTranscription(text)
        return
      }

      if (isExitCommand(text)) {
        exitVoiceMode()
        return
      }

      if (getIsVoiceMode()) {
        onTranscription(text)
      }
    } catch (err) {
      console.error('Transcription error:', err)
      if (getIsDictationMode()) {
        endDictation()
        console.error('Transcription failed')
      } else {
        console.error('Transcription failed')
        scheduleListenRetry('Transcription error, retrying...')
      }
    }
  }

  onCleanup(() => {
    stopRecording()
    stopAudioMonitor()
  })

  return {
    isRecording,
    startRecording,
    stopRecording,
  }
}

const EXIT_COMMANDS = ['stop listening', 'exit voice mode', 'stop voice mode', 'goodbye', 'bye']

function isExitCommand(text: string): boolean {
  const t = text.toLowerCase().trim()
  return EXIT_COMMANDS.includes(t)
}