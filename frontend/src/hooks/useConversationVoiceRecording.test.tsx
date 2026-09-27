import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createRoot, createSignal } from 'solid-js';

// Spy on the drainer. Mocking it (rather than stubbing the import away) keeps
// the real messageQueue processing flag observable, so we can assert the hook
// does not pre-empt the drainer by setting `processing` itself.
const triggerDrain = vi.fn(async () => {});
vi.mock('../services/queueDrainer', () => ({
  triggerDrain: (...args: unknown[]) => triggerDrain(...(args as [])),
}));

import { useConversationVoiceRecording } from './useConversationVoiceRecording';
import { getQueue, clearQueue, isProcessing, setProcessing, setActiveConversation } from '../state/messageQueue';
import { setVoice, setTtsSpeaking } from '../state/voice';

// ---------------------------------------------------------------------------
// Media API mocks
// ---------------------------------------------------------------------------
interface MockRecorder {
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  ondataavailable: ((e: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  onerror: ((e: Error) => void) | null;
  state: string;
}

const recorderInstances: MockRecorder[] = [];

function makeRecorder(): MockRecorder {
  const mr: MockRecorder = {
    start: vi.fn(),
    stop: vi.fn(() => {
      mr.state = 'inactive';
      // Emit a chunk then fire onstop, like a real recorder teardown.
      mr.ondataavailable?.({ data: new Blob([new Uint8Array(2048)], { type: 'audio/webm' }) });
      mr.onstop?.();
    }),
    ondataavailable: null,
    onstop: null,
    onerror: null,
    state: 'inactive',
  };
  recorderInstances.push(mr);
  return mr;
}

const mockAnalyser = {
  fftSize: 256,
  frequencyBinCount: 128,
  // Both analysers are mocked so a test can replay a frame exactly as the real
  // mic produced it. The detector reads time-domain data now; the byte data is
  // kept only for the regression test that replays the frame the old byte-mean
  // threshold wrongly called loud.
  getFloatTimeDomainData: vi.fn(),
  getByteFrequencyData: vi.fn(),
  connect: vi.fn(),
};

const mockAudioContext = {
  createAnalyser: vi.fn(() => mockAnalyser),
  createMediaStreamSource: vi.fn(() => ({ connect: vi.fn() })),
  close: vi.fn().mockResolvedValue(undefined),
  currentTime: 0,
  resume: vi.fn(),
};

Object.defineProperty(global, 'AudioContext', { value: vi.fn(() => mockAudioContext), writable: true });
Object.defineProperty(global, 'webkitAudioContext', { value: vi.fn(() => mockAudioContext), writable: true });
Object.defineProperty(global, 'MediaRecorder', { value: vi.fn(() => makeRecorder()), writable: true });
Object.defineProperty(MediaRecorder, 'isTypeSupported', { value: vi.fn(() => true), writable: true });
/** A MediaStream stand-in whose single track can be watched being released. */
function makeStream() {
  const track = { stop: vi.fn() };
  return { stream: { getTracks: () => [track] }, track };
}

Object.defineProperty(navigator, 'mediaDevices', {
  value: { getUserMedia: vi.fn().mockResolvedValue(makeStream().stream) },
  writable: true,
});

/** Reactive voice-mode flag; the hook's effect only re-runs on signal writes. */
function voiceModeSignal(initial: boolean) {
  const [get, set] = createSignal(initial);
  const sig = { get, set, is: () => get() };
  voiceModeSignals.push(sig);
  return sig;
}

// Every voice-mode signal is tracked so afterEach can switch it off first.
// Otherwise a previous test's hook survives disposal via its 500ms discard
// retry timer, grabs the mic again, and pollutes the next test's recorders.
const voiceModeSignals: Array<{ set: (v: boolean) => void }> = [];
const disposals: Array<() => void> = [];

/** Reactive turn-active flag, for tests that need the turn to start mid-capture. */
function turnActiveSignal(initial: boolean) {
  const [get, set] = createSignal(initial);
  return { get, set, is: () => get() };
}

/** Drive the hook inside a root, capturing its return value. */
function mountHook(opts: { isVoiceMode: () => boolean; isTurnActive: () => boolean }) {
  let hook!: ReturnType<typeof useConversationVoiceRecording>;
  const dispose = createRoot((d) => {
    hook = useConversationVoiceRecording(opts);
    return d;
  });
  disposals.push(dispose);
  return { hook, dispose };
}

async function flush(ms = 0) {
  await new Promise((r) => setTimeout(r, ms));
}

/**
 * Drive the analyser with a constant-amplitude signal of the given RMS. The
 * samples alternate around zero, so the RMS is exactly `rms` with no DC offset.
 */
function setLevel(rms: number) {
  mockAnalyser.getFloatTimeDomainData.mockImplementation((arr: Float32Array) => {
    for (let i = 0; i < arr.length; i++) arr[i] = i % 2 ? -rms : rms;
  });
}

/**
 * Replay a frame from the measured capture that the old detector called loud.
 * Its byte-spectrum mean was 0.0199 -- above the hardcoded 0.015 threshold --
 * while its time-domain RMS was 0.0006, which is the room's noise floor. 73.5%
 * of the silent frames in that capture were shaped like this, which is why the
 * silence timer never armed and a quiet room recorded to the 180s cap.
 */
function measuredNoiseFloorFrame() {
  mockAnalyser.getByteFrequencyData.mockImplementation((arr: Uint8Array) =>
    arr.fill(Math.round(0.0199 * 255)),
  );
  setLevel(0.0006);
}

/** Feed loud frames into the analyser. */
function speak() {
  setLevel(0.05);
}

/** Feed a silent (noise-floor) signal into the analyser. */
function goQuiet() {
  setLevel(0.0004);
}

/**
 * Reproduce the real user flow: speak, then fall silent long enough for the
 * hook's own silence detector to call stopRecording() and kick off
 * transcription. Returns once the recorder has been torn down.
 */
async function speakThenPause() {
  speak();
  await flush(600); // clear MIN_RECORDING_MS, bank MIN_AUDIO_FRAMES
  goQuiet();
  await flush(2100); // outlast SILENCE_DURATION so the hook stops itself
  await flush(20);
}

/** As speakThenPause, but the silence is a real frame from the capture. */
async function speakThenMeasuredPause() {
  speak();
  await flush(600);
  measuredNoiseFloorFrame();
  await flush(2100);
  await flush(20);
}

describe('useConversationVoiceRecording', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    voiceModeSignals.length = 0;
    disposals.length = 0;
    recorderInstances.length = 0;
    clearQueue();
    setProcessing(false);
    setActiveConversation('conv-1');
    setVoice({ status: 'idle', transcript: undefined, isDictating: false, isTtsSpeaking: false });
    mockAudioContext.close.mockClear();
    // Re-assert the default every test: a test that hands getUserMedia a
    // deferred implementation would otherwise leak into every later test.
    // Do NOT mockReset here -- that strips the default, and the hook's
    // setup-failure path then retries startRecording on every effect tick.
    navigator.mediaDevices.getUserMedia.mockClear();
    navigator.mediaDevices.getUserMedia.mockResolvedValue(makeStream().stream);
  });

  afterEach(async () => {
    // Silence every hook before disposal so no 500ms discard-retry timer
    // reopens the mic after this test has finished.
    voiceModeSignals.forEach((s) => s.set(false));
    disposals.forEach((d) => d());
    await flush(20);
    clearQueue();
    setProcessing(false);
    setActiveConversation(null);
  });

  it('starts recording when voice mode is switched on', async () => {
    const voiceMode = voiceModeSignal(false);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });

    await flush();
    expect(hook.isRecording()).toBe(false);

    voiceMode.set(true);
    await flush();

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalled();
    expect(hook.state()).toBe('recording');
    expect(hook.isRecording()).toBe(true);
    dispose();
  });

  // -------------------------------------------------------------------------
  // TTS / self-capture
  //
  // TTS is browser speechSynthesis, so while the agent answers out loud the mic
  // hears the agent. With the capture open, the VAD correctly hears speech, the
  // blob is transcribed, and the agent's own words are enqueued as a user turn
  // and sent straight back. The queue closes that loop automatically, with no
  // user action, which is how this reached the transcript.
  // -------------------------------------------------------------------------

  it('never opens the mic while the agent is speaking (regression: the agent transcribed its own TTS playback and answered itself)', async () => {
    const voiceMode = voiceModeSignal(true);
    setTtsSpeaking(true);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });

    await flush(600);
    speak(); // the agent's own voice, loud and continuous

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(hook.isRecording()).toBe(false);
    // No assertion on hook.state(): the old 'paused_tts' value asserted an
    // internal representation, and the gate being shut is fully described by the
    // two checks above. The policy no longer has a state for "voice mode on but
    // we must not record" -- that was the condition, not a state.
    dispose();
  });

  it('closes the mic and drops the audio when TTS starts mid-recording (regression: the partial blob was the agent speaking, and got enqueued as a user turn)', async () => {
    const voiceMode = voiceModeSignal(true);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();

    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ text: 'this should never be sent' }),
    })) as unknown as typeof fetch;

    // The user is mid-sentence when the agent starts talking over them.
    speak();
    await flush(600);
    expect(hook.isRecording()).toBe(true);

    setTtsSpeaking(true);
    await flush(50);

    // Mic closed, and the captured audio discarded rather than transcribed.
    expect(hook.isRecording()).toBe(false);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(getQueue()).toHaveLength(0);
    dispose();
  });

  it('reopens the mic when TTS ends, and only then', async () => {
    const voiceMode = voiceModeSignal(true);
    setTtsSpeaking(true);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });

    await flush();
    expect(hook.isRecording()).toBe(false);

    setTtsSpeaking(false);
    await flush();

    expect(hook.state()).toBe('recording');
    expect(hook.isRecording()).toBe(true);
    dispose();
  });

  // -------------------------------------------------------------------------
  // Speaking while the agent is busy
  //
  // This is the reason the queue exists: the user talks hands-free, the agent
  // starts working, and whatever was said in the meantime has to survive to be
  // sent once the agent is free. The backend log for a real attempt showed no
  // /transcribe request at all -- the mic had been shut for the whole turn, so
  // there was nothing to transcribe and nothing queued.
  // -------------------------------------------------------------------------

  it('keeps the mic open while the agent is processing (regression: the mic was shut for the entire turn, so speech during a turn was never captured)', async () => {
    const voiceMode = voiceModeSignal(true);
    const turnActive = turnActiveSignal(false);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: turnActive.is });

    await flush();
    expect(hook.isRecording()).toBe(true);

    // The drainer sends the previous utterance; the agent is now busy.
    turnActive.set(true);
    await flush(50);

    expect(hook.isRecording()).toBe(true);
    expect(hook.state()).toBe('recording');
    dispose();
  });

  it('opens the mic when voice mode starts during a turn (regression: only !turnActive could start a capture, so nothing was heard until the agent finished)', async () => {
    const voiceMode = voiceModeSignal(false);
    const turnActive = turnActiveSignal(true);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: turnActive.is });

    await flush(300);
    expect(hook.isRecording()).toBe(false);

    voiceMode.set(true);
    await flush();

    expect(hook.isRecording()).toBe(true);
    dispose();
  });

  it('transcribes and queues speech given while the agent is processing (regression: the whole point of the queue -- this produced no /transcribe call)', async () => {
    const voiceMode = voiceModeSignal(true);
    const turnActive = turnActiveSignal(false);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: turnActive.is });
    await flush();

    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ text: 'also, what is the weather tomorrow' }),
    })) as unknown as typeof fetch;

    // Agent picks up a turn; the user keeps talking over it.
    turnActive.set(true);
    await flush(50);
    expect(hook.isRecording()).toBe(true);

    await speakThenPause();

    expect(global.fetch).toHaveBeenCalledTimes(1);
    // Queued, not sent: the drainer refuses while the agent is busy, so it waits.
    expect(getQueue().map((m) => m.content)).toEqual(['also, what is the weather tomorrow']);
    expect(getQueue()[0].source).toBe('voice');
    dispose();
  });

  // -------------------------------------------------------------------------
  // Self-listening
  //
  // startRecording() is async: it awaits the old AudioContext closing and then
  // getUserMedia, which is a permission prompt on a real machine. isRecording
  // only flips at the very END of that setup. So for the whole of the await
  // window the hook claimed to be recording while nothing had actually opened,
  // and both the discard path and the re-entrancy guard read false. A capture
  // granted the mic after the agent had started talking was then started
  // anyway, and the agent transcribed itself.
  // -------------------------------------------------------------------------

  it('drops the capture when the mic is granted after the agent starts speaking (regression: the recorder started while TTS was playing, so it captured the agent)', async () => {
    const voiceMode = voiceModeSignal(true);
    const { stream, track } = makeStream();
    let grant!: () => void;
    navigator.mediaDevices.getUserMedia.mockImplementation(
      () => new Promise((r) => { grant = () => r(stream); }),
    );

    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();
    // The mic has not been granted yet, so nothing is recording.
    expect(hook.isRecording()).toBe(false);
    expect(recorderInstances.length).toBe(0);

    // The agent begins talking while the mic is still being opened.
    setTtsSpeaking(true);
    await flush();
    grant();
    await flush();

    expect(hook.isRecording()).toBe(false);
    expect(recorderInstances.length).toBe(0);
    // And the track we were handed must be handed back, or the mic stays hot.
    expect(track.stop).toHaveBeenCalled();
    dispose();
  });

  it('opens the mic once when a second capture is asked for before the first one is granted (regression: two MediaRecorders on one mic sent the same audio twice)', async () => {
    const voiceMode = voiceModeSignal(true);
    const { stream } = makeStream();
    let grant!: () => void;
    navigator.mediaDevices.getUserMedia.mockImplementation(
      () => new Promise((r) => { grant = () => r(stream); }),
    );

    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);

    // Voice mode is toggled off and back on before the mic is granted. The
    // recording -> idle -> recording detour asks for a capture a second time.
    voiceMode.set(false);
    voiceMode.set(true);
    await flush();

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
    grant();
    await flush();

    expect(recorderInstances.length).toBe(1);
    expect(hook.isRecording()).toBe(true);
    dispose();
  });

  it('stops itself on a real measured silence frame (regression: 73.5% of silent frames read loud on the old byte-mean threshold, so the silence timer never armed and recording ran to the 180s cap)', async () => {
    const voiceMode = voiceModeSignal(true);
    const { dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();

    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ text: 'the weather is fine' }),
    })) as unknown as typeof fetch;

    // Captured before the pause: once transcription finishes the hook reopens the
    // mic, so the last instance by then is a fresh recorder this test never stops.
    const mr = recorderInstances[recorderInstances.length - 1];

    await speakThenMeasuredPause();

    // It has to be the silence detector that stopped this, not the 180s cap, and
    // it has to be transcribing as a result.
    expect(mr.stop).toHaveBeenCalled();
    expect(getQueue()).toHaveLength(1);
    dispose();
  });

  it('enqueues the transcription and triggers a drain (regression: undefined triggerDrain stranded the queue)', async () => {
    const voiceMode = voiceModeSignal(true);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });

    await flush();
    expect(hook.isRecording()).toBe(true);

    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ text: 'what is the weather today' }),
    })) as unknown as typeof fetch;

    hook.state(); // touch accessor
    // Simulate the recorder finishing: push a loud frame then stop.
    const mr = recorderInstances[recorderInstances.length - 1];
    speak();
    await flush(600); // exceed MIN_RECORDING_MS, accumulate MIN_AUDIO_FRAMES
    mr.stop();

    await flush(50);

    expect(getQueue()).toHaveLength(1);
    expect(getQueue()[0].content).toBe('what is the weather today');
    expect(getQueue()[0].source).toBe('voice');
    expect(triggerDrain).toHaveBeenCalledTimes(1);
    dispose();
  });

  it('does not mark the queue processing before the drainer runs (regression: drainQueueIfReady bails when isProcessing() is true)', async () => {
    const voiceMode = voiceModeSignal(true);
    let processingAtDrainTime: boolean | null = null;
    triggerDrain.mockImplementation(async () => {
      processingAtDrainTime = isProcessing();
    });

    const { dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();

    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ text: 'hello assistant' }),
    })) as unknown as typeof fetch;

    const mr = recorderInstances[recorderInstances.length - 1];
    speak();
    await flush(600);
    mr.stop();
    await flush(50);

    expect(triggerDrain).toHaveBeenCalled();
    expect(processingAtDrainTime).toBe(false);
    dispose();
  });

  it('holds the mic closed while /transcribe is in flight (regression: reopened mid-request)', async () => {
    const voiceMode = voiceModeSignal(true);
    let resolveFetch: ((v: unknown) => void) | null = null;
    const fetchPromise = new Promise((r) => {
      resolveFetch = r;
    });

    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();

    global.fetch = vi.fn(async () => {
      await fetchPromise;
      return { ok: true, status: 200, json: async () => ({ text: 'slow response' }) };
    }) as unknown as typeof fetch;

    const mr = recorderInstances[recorderInstances.length - 1];
    expect(mr).toBeDefined();
    await speakThenPause();

    // Request outstanding: must be transcribing, NOT recording.
    expect(hook.state()).toBe('transcribing');
    expect(hook.isRecording()).toBe(false);

    const countWhilePending = recorderInstances.length;

    resolveFetch!({});
    await flush(50);

    // Only now may the mic reopen.
    expect(hook.state()).toBe('recording');
    expect(recorderInstances.length).toBeGreaterThan(countWhilePending);
    dispose();
  });

  it('leaves the processing flag clear so later utterances can still drain', async () => {
    const voiceMode = voiceModeSignal(true);
    const { dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();

    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ text: 'queue me' }),
    })) as unknown as typeof fetch;

    const mr = recorderInstances[recorderInstances.length - 1];
    speak();
    await flush(600);
    mr.stop();
    await flush(50);

    expect(isProcessing()).toBe(false);
    dispose();
  });

  it('releases the mic gate when transcription fails', async () => {
    const voiceMode = voiceModeSignal(true);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();

    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 500,
      json: async () => ({ detail: 'boom' }),
    })) as unknown as typeof fetch;

    const mr = recorderInstances[recorderInstances.length - 1];
    speak();
    await flush(600);
    mr.stop();
    await flush(600); // handleDiscard retries after 500ms

    expect(getQueue()).toHaveLength(0);
    expect(hook.state()).not.toBe('transcribing');
    dispose();
  });

  it('stops recording when voice mode is switched off', async () => {
    const voiceMode = voiceModeSignal(true);
    const { hook, dispose } = mountHook({ isVoiceMode: voiceMode.is, isTurnActive: () => false });
    await flush();
    expect(hook.isRecording()).toBe(true);

    voiceMode.set(false);
    await flush();

    expect(hook.isRecording()).toBe(false);
    expect(hook.state()).toBe('idle');
    dispose();
  });
});
