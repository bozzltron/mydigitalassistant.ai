/**
 * Shared audio helpers for the two recording hooks.
 *
 * `useVoiceRecording` (dictation) and `useConversationVoiceRecording`
 * (continuous voice) each carried their own copy of the earcon generator, the
 * MIME negotiation, the silence/max-recording constants and the exit-command
 * check. The copies had already drifted once, so they live here now.
 */
import { settings } from '../state/settings'

export const SILENCE_DURATION = 2000
export const MIN_RECORDING_MS = 500
export const MIN_AUDIO_FRAMES = 3
export const MAX_RECORDING_MS = 180000
export const MONITOR_INTERVAL_MS = 80

const EXIT_COMMANDS = ['stop listening', 'exit voice mode', 'stop voice mode', 'goodbye', 'bye']

/** True when a transcript is a spoken command to leave voice mode. */
export function isExitCommand(text: string): boolean {
  const t = text.toLowerCase().trim()
  return EXIT_COMMANDS.includes(t)
}

type AudioContextWindow = { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }

/** AudioContext constructor across prefixed browsers, or undefined. */
export function getAudioContextCtor(): typeof AudioContext | undefined {
  if (typeof window === 'undefined') return undefined
  const w = window as unknown as AudioContextWindow
  return w.AudioContext ?? w.webkitAudioContext
}

/** Best progressive MIME type the browser can record, or null if unsupported. */
export function selectMimeType(): string | null {
  if (typeof MediaRecorder === 'undefined') return null
  return (
    MediaRecorder.isTypeSupported('audio/ogg') ? 'audio/ogg' :
    MediaRecorder.isTypeSupported('audio/wav') ? 'audio/wav' :
    MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' :
    MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' :
    null
  )
}

/** Short earcon for recording start/stop/error; no-op unless enabled in settings. */
export function playEarcon(type: 'start' | 'stop' | 'error'): void {
  if (!settings.soundEffectsEnabled) return
  const AudioContextCtor = getAudioContextCtor()
  if (!AudioContextCtor) return
  try {
    const ctx = new AudioContextCtor()
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
    // Earcons are cosmetic; never let one break recording.
  }
}
