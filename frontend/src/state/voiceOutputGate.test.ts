import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  speakReplacing,
  cancelSpeech,
  isOutputActive,
  isOutputActiveNow,
  outputGeneration,
  setVoice,
} from './voice'

// ---------------------------------------------------------------------------
// The output gate
//
// speechSynthesis.speak() queues rather than interrupts. That single fact caused
// three separate failures, all of which show up as the agent recording itself:
//
//   1. The half-duplex window grew without bound. Every queued answer added its
//      full duration to a period during which the mic had to stay shut, and with
//      message queuing on top, each exchange added to the one before it.
//   2. The gate was one boolean for the whole queue, so it opened in the gap
//      between one utterance's onend and the next one's onstart -- while the
//      next utterance was already audible.
//   3. A cancelled utterance still fires onend/onerror, asynchronously. Those
//      events belong to speech that is no longer happening, but they arrived at
//      the same shared boolean, so a stale end could open the gate in the middle
//      of a sentence.
//
// The fix is control flow, not detection: one utterance in flight, newest wins,
// and every lifecycle event tagged so a stale one is recognisable as stale.
// ---------------------------------------------------------------------------

class FakeUtterance {
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: ((e: unknown) => void) | null = null
  rate = 1
  pitch = 1
  volume = 1
  voice: unknown = null
  constructor(public text: string) {}
}

/** Utterances handed to speak() that have not been cancelled. */
let inFlight: FakeUtterance[] = []

const synth = {
  speaking: false,
  pending: false,
  speak: vi.fn((u: FakeUtterance) => {
    inFlight.push(u)
  }),
  cancel: vi.fn(() => {
    // A real cancel does not fire onend synchronously -- the event is queued --
    // which is exactly why a stale end could land after the next start.
    inFlight = []
  }),
  getVoices: () => [],
}

Object.defineProperty(window, 'speechSynthesis', { value: synth, writable: true })
Object.defineProperty(window, 'SpeechSynthesisUtterance', {
  value: FakeUtterance,
  writable: true,
})
Object.defineProperty(globalThis, 'SpeechSynthesisUtterance', {
  value: FakeUtterance,
  writable: true,
})

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
/** Long enough for TAIL_HOLD_MS to expire. */
const TAIL = 500

describe('output gate', () => {
  beforeEach(async () => {
    synth.speak.mockClear()
    synth.cancel.mockClear()
    synth.speaking = false
    synth.pending = false
    inFlight = []
    setVoice({ status: 'idle', transcript: undefined, isDictating: false, isTtsSpeaking: false })
    // The gate is module state with a live tail timer, so let any timer left by
    // the previous test expire before asserting a clean starting point.
    await sleep(TAIL)
  })

  it('speaks the newest message instead of queueing behind the one still playing', () => {
    speakReplacing('first answer')
    expect(synth.speak).toHaveBeenCalledTimes(1)
    expect(synth.speak.mock.calls[0][0].text).toBe('first answer')

    const first = synth.speak.mock.calls[0][0]
    first.onstart?.()

    // A second answer arrives mid-sentence.
    speakReplacing('second answer')

    // The old code called speak() straight through, so both played: the window
    // during which the mic had to stay shut was the sum of both answers. The
    // replacement cancels first, so only one is ever live.
    expect(synth.speak).toHaveBeenCalledTimes(2)
    expect(synth.speak.mock.calls[1][0].text).toBe('second answer')
    expect(synth.cancel).toHaveBeenCalledTimes(2) // once per request, before speaking
    expect(inFlight).toHaveLength(1)
    expect(inFlight[0].text).toBe('second answer')
  })

  it('closes the gate as soon as speech is requested, not when it starts', () => {
    speakReplacing('answer')
    // There is real latency between speak() and the first syllable. Holding the
    // gate only from onstart left that latency open for the microphone.
    expect(isOutputActive()).toBe(true)
  })

  it('ignores the end event of an utterance that was already replaced', () => {
    speakReplacing('first answer')
    const first = synth.speak.mock.calls[0][0]
    first.onstart?.()

    speakReplacing('second answer')
    const second = synth.speak.mock.calls[1][0]
    second.onstart?.()

    // The cancelled utterance's end event arrives now -- after the replacement
    // has already started. It describes speech that is no longer happening, and
    // honouring it opened the microphone in the middle of a sentence.
    first.onend?.()
    first.onerror?.(new Error('cancelled'))

    expect(isOutputActive()).toBe(true)
  })

  it('releases the gate when the current utterance ends', async () => {
    speakReplacing('answer')
    const only = synth.speak.mock.calls[0][0]
    only.onstart?.()
    only.onend?.()

    expect(isOutputActive()).toBe(true) // still the reverb tail
    await sleep(TAIL)
    expect(isOutputActive()).toBe(false)
  })

  it('holds the gate shut for the tail after the speech stops', async () => {
    speakReplacing('answer')
    const only = synth.speak.mock.calls[0][0]
    only.onstart?.()
    only.onend?.()

    // Reopening the microphone on the agent's last syllable's reverb is how a
    // tail gets captured, so the gate outlives the audio.
    await sleep(TAIL - 250)
    expect(isOutputActive()).toBe(true)
  })

  it('holds the gate shut after the user stops the agent mid-sentence', async () => {
    speakReplacing('a long answer that gets interrupted')
    const only = synth.speak.mock.calls[0][0]
    only.onstart?.()

    cancelSpeech()
    expect(synth.cancel).toHaveBeenCalled()

    // The old stop button called speechSynthesis.cancel() and setTtsSpeaking(false)
    // with nothing in between, so the mic opened on the cut-off syllable.
    expect(isOutputActive()).toBe(true)
    await sleep(TAIL)
    expect(isOutputActive()).toBe(false)
  })

  it('reopens promptly when speech starts again during the tail', async () => {
    speakReplacing('first answer')
    synth.speak.mock.calls[0][0].onend?.()
    expect(isOutputActive()).toBe(true)

    // A new answer lands before the tail has expired. The pending timer must not
    // be left to fire and open the gate mid-sentence.
    await sleep(100)
    speakReplacing('second answer')
    const second = synth.speak.mock.calls[1][0]
    second.onstart?.()
    second.onend?.()

    await sleep(TAIL)
    expect(isOutputActive()).toBe(false)
  })

  it('trusts the platform when our own bookkeeping has not seen the speech', () => {
    // isOutputActive() reads signals, so it is only as current as the last event
    // we handled. The hooks use the imperative form at the one point where the
    // platform's own answer is the fresh one -- immediately after awaiting
    // getUserMedia.
    synth.speaking = true
    expect(isOutputActive()).toBe(false)
    expect(isOutputActiveNow()).toBe(true)

    synth.speaking = false
    synth.pending = true
    expect(isOutputActiveNow()).toBe(true)
  })

  it('moves the generation on every speaker transition', () => {
    // The recording hooks snapshot this before awaiting getUserMedia and compare
    // after. A boolean read is stale the instant the await suspends; only a
    // counter proves the speaker moved at all while we were away.
    const before = outputGeneration()
    speakReplacing('answer')
    expect(outputGeneration()).toBeGreaterThan(before)
  })
})
