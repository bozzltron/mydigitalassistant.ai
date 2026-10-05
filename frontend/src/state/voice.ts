import { createSignal } from 'solid-js'
import { createStore } from 'solid-js/store'

// Voice state type definition - matching original chat.html
export type VoiceStatus = 'idle' | 'listening' | 'processing' | 'speaking' | 'error'

export interface VoiceState {
  status: VoiceStatus
  transcript?: string
  isDictating: boolean
  isTtsSpeaking: boolean
}

// Create store for voice state (allows partial updates)
export const [voice, setVoice] = createStore<VoiceState>({
  status: 'idle',
  transcript: undefined,
  isDictating: false,
  isTtsSpeaking: false,
})

// Callback for external stop recording (registered by useVoiceRecording hook)
let stopRecordingCallback: (() => void) | null = null

export const registerStopRecording = (fn: () => void) => {
  stopRecordingCallback = fn
}

export const unregisterStopRecording = () => {
  stopRecordingCallback = null
}

export const stopRecording = () => {
  if (stopRecordingCallback) {
    stopRecordingCallback()
  }
}

// Voice actions - proper state machine
export const enterVoiceMode = () => {
  setVoice('status', 'listening')
}

export const exitVoiceMode = () => {
  setVoice('status', 'idle')
  setVoice('isDictating', false)
  setVoice('transcript', undefined)
}

export const startListening = () => {
  setVoice('status', 'listening')
}

export const startProcessing = () => {
  setVoice('status', 'processing')
}

export const startSpeaking = () => {
  setVoice('status', 'speaking')
}

export const setError = (error: string) => {
  setVoice('status', 'error')
  setVoice('transcript', error)
}

// Dictation mode (one-shot recording)
export const startDictation = () => {
  setVoice('status', 'listening')
  setVoice('isDictating', true)
}

export const endDictation = () => {
  setVoice('status', 'idle')
  setVoice('isDictating', false)
  setVoice('transcript', undefined)
}

// Helper to get computed state for UI
export const isListening = () => voice.status === 'listening'
export const isProcessing = () => voice.status === 'processing'
// Speaking includes both voice mode speaking and TTS playback
export const isSpeaking = () => voice.status === 'speaking' || voice.isTtsSpeaking
export const isIdle = () => voice.status === 'idle'
export const isError = () => voice.status === 'error'
export const isTtsSpeaking = () => voice.isTtsSpeaking

// Voice mode is active during listening AND processing (transcription), but NOT
// during one-shot dictation. Dictation reuses the 'listening' status, so without
// excluding it the continuous-capture hook treated a dictation press as voice
// mode and opened the mic for hands-free conversation.
export const isVoiceModeActive = () =>
  !voice.isDictating &&
  (voice.status === 'listening' || voice.status === 'processing')

// TTS speaking state setters
export const setTtsSpeaking = (speaking: boolean) => {
  setVoice('isTtsSpeaking', speaking)
}

// ---------------------------------------------------------------------------
// Output gate
//
// Strict half-duplex: the mic is open when voice mode is on and the agent is
// not speaking, and at no other time. Barge-in is deliberately unsupported --
// the stop button is the escape hatch.
//
// Half-duplex is what makes echo a non-problem. We never ask "was that
// transcript the agent's own voice?", because that question has no useful
// answer: a transcript of synthetic speech is a paraphrase, not a copy. Scored
// against the text it came from, real TTS echo landed at 0.07 word-trigram
// similarity, while a real user turn asking a near-identical question scored
// 0.52 -- every lexical measure ranked genuine speech above genuine echo. So
// rather than detect the leak we remove the possibility: never let the capture
// path and the output path overlap.
//
// This module is the single owner of that decision. The two recording hooks
// previously each implemented their own `paused_tts` state to express the same
// rule, and the copies had already drifted -- the conversation hook had no TTS
// awareness at all until it was caught listening to the agent.
// ---------------------------------------------------------------------------

/**
 * How long the gate stays shut after speech stops. The room is still ringing;
 * reopening the mic on the agent's last syllable's reverb is how a tail gets
 * captured.
 */
const TAIL_HOLD_MS = 400

/**
 * Bumped on every speaker transition. The recording hooks snapshot it before
 * awaiting getUserMedia() and compare after: a boolean read is stale the instant
 * the await suspends, whereas a counter detects that the speaker moved at all
 * while we were away. It doubles as the utterance identity check, so a stale
 * lifecycle event from a cancelled utterance is recognisable as stale.
 */
let generation = 0

// Reactive so the recording hooks' effects re-run when playback starts or ends.
const [outputHeld, setOutputHeld] = createSignal(false)
let tailTimer: number | null = null

// The utterance currently in flight, held so it cannot be garbage-collected
// while the platform is still speaking it. A GC'd utterance stops mid-sentence
// and never fires onend/onerror, which leaves `isTtsSpeaking` stuck true -- the
// output gate stays shut and the microphone never reopens. Browsers are not
// required to keep a strong reference; holding one here is the documented fix.
let currentUtterance: SpeechSynthesisUtterance | null = null

/**
 * The platform's own view of whether it is emitting. Consulted imperatively at
 * decision points, because it is the one reading that is valid at the instant it
 * is made -- our event handlers are asynchronous and can be overtaken.
 */
const platformIsSpeaking = () =>
  typeof window !== 'undefined' &&
  'speechSynthesis' in window &&
  (window.speechSynthesis.speaking || window.speechSynthesis.pending)

export const beginOutput = () => {
  generation += 1
  if (tailTimer !== null) {
    clearTimeout(tailTimer)
    tailTimer = null
  }
  setOutputHeld(true)
  setVoice('isTtsSpeaking', true)
}

export const endOutput = () => {
  generation += 1
  currentUtterance = null
  setVoice('isTtsSpeaking', false)
  if (tailTimer !== null) clearTimeout(tailTimer)
  tailTimer = window.setTimeout(() => {
    tailTimer = null
    setOutputHeld(false)
  }, TAIL_HOLD_MS)
}

/** True whenever the agent may be speaking. Reactive. */
export const isOutputActive = () => isTtsSpeaking() || outputHeld()

/**
 * The same question asked imperatively, for use immediately before and after an
 * await. `isOutputActive` reads signals, and a signal read before a suspension
 * is stale after it; this also consults the platform directly.
 */
export const isOutputActiveNow = () => isOutputActive() || platformIsSpeaking()

/** Snapshot before an await; compare against it once it resolves. */
export const outputGeneration = () => generation

/** Stop playback now and hold the gate for the reverb tail. */
export const cancelSpeech = () => {
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
    window.speechSynthesis.cancel()
  }
  endOutput()
}

/**
 * Speak `text`, replacing anything currently in flight.
 *
 * speechSynthesis.speak() queues, so speaking a second message while the first
 * is still audible leaves both playing and stretches the half-duplex window to
 * the sum of both. Cancelling first keeps exactly one utterance in flight, which
 * bounds the window to a single message and makes "the agent is speaking" one
 * well-defined interval instead of an open-ended queue.
 *
 * A cancelled utterance still fires onend/onerror, asynchronously. Those events
 * belong to a speech that is no longer happening, so they must not open the gate
 * for whatever is speaking now -- hence the generation tag on every handler.
 */
export const speakReplacing = (
  text: string,
  configure?: (utterance: SpeechSynthesisUtterance) => void,
): number => {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return generation
  window.speechSynthesis.cancel()
  const tag = (beginOutput(), generation)
  const utterance = new SpeechSynthesisUtterance(text)
  configure?.(utterance)
  // Some engines (notably Firefox) pick no voice at all when neither `voice`
  // nor `lang` is set, and the utterance is silently dropped. The caller sets
  // `voice` when it can resolve the saved URI; this is the fallback.
  if (!utterance.lang) utterance.lang = navigator.language || 'en-US'
  const release = () => {
    if (currentUtterance === utterance) currentUtterance = null
    if (generation === tag) endOutput()
  }
  utterance.onend = release
  utterance.onerror = release
  currentUtterance = utterance
  window.speechSynthesis.speak(utterance)
  return tag
}