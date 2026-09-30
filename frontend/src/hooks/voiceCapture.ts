import { batch, createSignal } from 'solid-js'
import { debug } from '../services/logger'
import { VoiceActivityDetector } from '../services/voiceActivity'
import {
  MAX_RECORDING_MS,
  MIN_AUDIO_FRAMES,
  MIN_RECORDING_MS,
  MONITOR_INTERVAL_MS,
  SILENCE_DURATION,
  getAudioContextCtor,
  selectMimeType,
} from '../services/audio'
import { isOutputActiveNow, outputGeneration } from '../state/voice'

/**
 * Shared microphone capture.
 *
 * Extracted from `useVoiceRecording` and `useConversationVoiceRecording`, which
 * each carried their own copy of the getUserMedia/AudioContext/MediaRecorder
 * lifecycle, the VAD monitor, the silence and max-duration timers, MIME
 * selection, half-duplex (output-generation) gating, and blob assembly. The two
 * copies had already drifted once (one had no TTS guard and transcribed the
 * agent's own playback).
 *
 * This owns *capture*; each hook keeps its own *policy* (when to open the mic,
 * and what to do with a transcript).
 */
export interface VoiceCaptureCallbacks {
  /** Short tag for logs, e.g. `[convVoice]`. */
  label: string
  /** True while a capture is still wanted (voice/dictation mode on). */
  shouldCapture: () => boolean
  /** A completed, valid capture, assembled into a blob. */
  onCapture: (blob: Blob, mime: string) => void | Promise<void>
  /** The capture was thrown away (silence, too short, or dropped). */
  onDiscard: (reason: string) => void
  /** The recorder started and is now live. */
  onStarted?: () => void
  /** getUserMedia / AudioContext / MIME setup failed. */
  onStartError?: (error: unknown) => void
  /** The MediaRecorder faulted after starting. */
  onRecorderError?: (error: unknown) => void
  /** The capture was handed straight back (mode ended / TTS started mid-open). */
  onStartDropped?: () => void
}

export interface VoiceCapture {
  isRecording: () => boolean
  /** True from closing the recorder until its fate is decided. */
  isSettling: () => boolean
  /** Bumps whenever a capture starts, ends, or is handed back. */
  epoch: () => number
  start: () => Promise<void>
  stop: () => void
  discard: () => void
  cleanup: () => void
}

export function createVoiceCapture(cb: VoiceCaptureCallbacks): VoiceCapture {
  const [mediaRecorder, setMediaRecorder] = createSignal<MediaRecorder | null>(null)
  const [audioChunks, setAudioChunks] = createSignal<Blob[]>([])
  const [isRecording, setIsRecording] = createSignal(false)
  const [isSettling, setIsSettling] = createSignal(false)
  const [currentMimeType, setCurrentMimeType] = createSignal<string | null>(null)
  const [audioContext, setAudioContext] = createSignal<AudioContext | null>(null)
  const [analyser, setAnalyser] = createSignal<AnalyserNode | null>(null)
  const [mediaStream, setMediaStream] = createSignal<MediaStream | null>(null)
  const [silenceTimeout, setSilenceTimeout] = createSignal<number | null>(null)
  const [recordingTimeoutId, setRecordingTimeoutId] = createSignal<number | null>(null)
  const [recordingStartTime, setRecordingStartTime] = createSignal(0)
  const [loudFrameCount, setLoudFrameCount] = createSignal(0)
  const [silenceAfterLoud, setSilenceAfterLoud] = createSignal(false)
  const [monitorIntervalId, setMonitorIntervalId] = createSignal<number | null>(null)
  const [epoch, setEpoch] = createSignal(0)
  const [pendingTimeouts, setPendingTimeouts] = createSignal<Set<number>>(new Set())

  // Owns the silence decision and the adaptive noise floor. Reset at the start
  // of every recording so one turn's calibration never leaks into the next.
  const vad = new VoiceActivityDetector()

  // True from the first line of start() until it returns or throws, set before
  // any await. isRecording() only becomes true at the very end, so without this
  // a second start() walks past the guard and opens a second microphone.
  let startingUp = false
  // Set when a recording is being thrown away rather than transcribed.
  let discardNextCapture = false
  // A capture may be submitted at most once. The backend log showed a single
  // blob POSTed twice, byte-identical, so the second submission was pure loss.
  let submittedCapture = false

  function addTimeout(id: number) {
    setPendingTimeouts(prev => new Set(prev).add(id))
  }

  function bumpEpoch() {
    setEpoch(n => n + 1)
  }

  function checkAudioLevels() {
    const reading = vad.read(analyser())
    if (!reading) return

    const elapsed = Date.now() - recordingStartTime()
    const metMinDuration = elapsed >= MIN_RECORDING_MS

    if (reading.speech) {
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
      }
      if (!silenceTimeout() && metMinDuration && loudFrameCount() >= MIN_AUDIO_FRAMES) {
        const st = window.setTimeout(() => {
          if (isRecording()) {
            debug(`${cb.label} silence timeout fired — stopping`)
            stop()
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

  function releaseStream() {
    const ms = mediaStream()
    if (ms) {
      ms.getTracks().forEach(t => t.stop())
      setMediaStream(null)
    }
  }

  function releaseContext() {
    const ctx = audioContext()
    if (ctx) {
      ctx.close().catch(() => {})
      setAudioContext(null)
      setAnalyser(null)
    }
  }

  async function start(): Promise<void> {
    debug(`${cb.label} start called`)
    // isRecording() is not a sufficient guard. It only becomes true at the very
    // end of this function, so for the whole of the awaits below the capture
    // looked idle and a second start() opened a second microphone, sending the
    // same audio twice. Set synchronously, before any suspension point.
    if (isRecording() || startingUp) return
    startingUp = true

    // Half-duplex has to survive the awaits below, not merely be intended.
    // Opening a microphone takes long enough -- a permission prompt, a cold
    // AudioContext -- for the agent to start answering. Snapshot the speaker
    // before suspending and re-check after: a boolean is stale the moment we
    // yield, whereas a changed generation proves the speaker moved.
    const spokeFor = outputGeneration()
    let stream: MediaStream | null = null
    let started = false
    let dropped = false
    try {
      const existingCtx = audioContext()
      if (existingCtx) {
        await existingCtx.close().catch(() => {})
      }

      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      debug(`${cb.label} got media stream`, stream.getTracks())

      // The mic was granted, but the world moved on while we waited. Starting
      // now would put an open microphone in a room where the agent is talking.
      if (!cb.shouldCapture() || isOutputActiveNow() || outputGeneration() !== spokeFor) {
        debug(`${cb.label} dropping capture — mode ended or the agent started speaking while the mic was opening`)
        dropped = true
        return
      }

      setMediaStream(stream)

      const AudioContextCtor = getAudioContextCtor()
      if (!AudioContextCtor) {
        setMediaStream(null)
        cb.onStartError?.(new Error('AudioContext unavailable'))
        return
      }
      const newAudioContext = new AudioContextCtor()
      setAudioContext(newAudioContext)

      const newAnalyser = newAudioContext.createAnalyser()
      newAnalyser.fftSize = 256
      setAnalyser(newAnalyser)

      newAudioContext.createMediaStreamSource(stream).connect(newAnalyser)

      const mimeType = selectMimeType()
      if (!mimeType) {
        console.error('Audio recording not supported in this browser')
        cb.onStartError?.(new Error('recording unsupported'))
        return
      }
      debug(`${cb.label} selected mimeType:`, mimeType)

      const mr = new MediaRecorder(stream, { mimeType })
      setMediaRecorder(mr)
      setAudioChunks([])
      setCurrentMimeType(mimeType)
      submittedCapture = false

      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          setAudioChunks(prev => [...prev, e.data])
        }
      }

      mr.onstop = async () => {
        try {
          if (discardNextCapture) {
            // Cleared here rather than in discard() so it cannot leak into a
            // later, legitimate capture.
            discardNextCapture = false
            setAudioChunks([])
            return
          }
          const elapsed = Date.now() - recordingStartTime()
          if (audioChunks().length === 0) {
            cb.onDiscard("Didn't catch that")
            return
          }
          if (elapsed < MIN_RECORDING_MS || loudFrameCount() < MIN_AUDIO_FRAMES) {
            cb.onDiscard("Didn't catch that")
            return
          }
          const mime = currentMimeType() || 'audio/webm'
          const blob = new Blob(audioChunks(), { type: mime })
          setAudioChunks([])
          if (submittedCapture) return
          submittedCapture = true
          await cb.onCapture(blob, mime)
        } catch (err) {
          console.error(`${cb.label} recording stop error:`, err)
          cb.onDiscard('Processing error')
        } finally {
          // Every exit passes through here, including the discard and the error
          // paths, so the gate cannot be left shut forever.
          setIsSettling(false)
          bumpEpoch()
        }
      }

      mr.onerror = (e) => {
        console.error('MediaRecorder error:', e)
        cb.onRecorderError?.(e)
      }

      mr.start()
      started = true
      setIsRecording(true)
      setRecordingStartTime(Date.now())
      setLoudFrameCount(0)
      setSilenceAfterLoud(false)
      vad.reset()
      cb.onStarted?.()

      startAudioMonitor()

      const timeoutId = window.setTimeout(() => {
        if (isRecording()) stop()
      }, MAX_RECORDING_MS)
      setRecordingTimeoutId(timeoutId)
      addTimeout(timeoutId)
    } catch (e) {
      console.warn(`${cb.label} failed to start recording:`, e)
      cb.onStartError?.(e)
    } finally {
      startingUp = false
      // A microphone we were granted but did not use must not be left open --
      // the recording indicator would stay lit with nothing running.
      if (stream && !started) {
        stream.getTracks().forEach(t => t.stop())
        if (dropped) cb.onStartDropped?.()
        bumpEpoch()
      }
    }
  }

  function stop() {
    const mr = mediaRecorder()
    if (!isRecording() || !mr) return

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

    // One atomic teardown, and `settling` goes up *inside* the batch. Solid
    // flushes effects after each individual signal write, so clearing
    // isRecording first would let a policy run against a half-closed capture --
    // a closed mic with nothing yet holding the gate -- and open a second
    // capture on top of the one being torn down.
    batch(() => {
      setIsSettling(true)
      setMediaRecorder(null)
      setIsRecording(false)
      setSilenceAfterLoud(false)
    })

    try {
      mr.stop()
    } catch (e) {
      console.warn('Error stopping recorder:', e)
      // onstop will never fire, so nothing else would ever clear this.
      setIsSettling(false)
    }

    releaseStream()
    releaseContext()
  }

  // Close the mic without transcribing what it captured. Used when the agent
  // starts speaking mid-recording: the tail is the agent's own voice, and
  // sending it to /transcribe would enqueue it as a user turn.
  function discard() {
    if (!isRecording()) return
    discardNextCapture = true
    stop()
  }

  function cleanup() {
    // A capture open at teardown must be closed (the recording indicator would
    // otherwise stay lit), and discarded so its onstop does not transcribe after
    // the hook is gone.
    if (isRecording()) {
      discardNextCapture = true
      stop()
    }

    stopAudioMonitor()
    releaseStream()
    releaseContext()
    setMediaRecorder(null)
    setAudioChunks([])

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

    pendingTimeouts().forEach(id => clearTimeout(id))
    setPendingTimeouts(new Set<number>())
  }

  return {
    isRecording,
    isSettling,
    epoch,
    start,
    stop,
    discard,
    cleanup,
  }
}
