import { createSignal, createEffect, onCleanup, batch } from 'solid-js';
import { enqueue, isProcessing } from '../state/messageQueue';
import { triggerDrain } from '../services/queueDrainer';
import { VoiceActivityDetector } from '../services/voiceActivity';
import { settings } from '../state/settings';
import { exitVoiceMode, isOutputActive, isOutputActiveNow, outputGeneration } from '../state/voice';

const SILENCE_DURATION = 2000;
const MIN_RECORDING_MS = 500;
const MIN_AUDIO_FRAMES = 3;
const MAX_RECORDING_MS = 180000;
const MONITOR_INTERVAL_MS = 80;

// How long to stay shut after the microphone fails to open. Long enough that a
// transient failure is ridden out, short enough that a transient failure does not
// feel like a dead hook.
const CAPTURE_RETRY_MS = 2000;

type ConvVoiceState = 'idle' | 'recording' | 'transcribing';

interface UseConversationVoiceRecordingOptions {
  isVoiceMode: () => boolean;
  isTurnActive: () => boolean;
}

interface UseConversationVoiceRecordingReturn {
  isRecording: () => boolean;
  state: () => ConvVoiceState;
}

function playEarcon(type: 'start' | 'stop' | 'error') {
  if (!settings.soundEffectsEnabled) return;
  if (!window.AudioContext && !window.webkitAudioContext) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    const now = ctx.currentTime;
    if (type === 'start') {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.exponentialRampToValueAtTime(1760, now + 0.08);
      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
      osc.start(now);
      osc.stop(now + 0.12);
    } else if (type === 'stop') {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(1760, now);
      osc.frequency.exponentialRampToValueAtTime(880, now + 0.08);
      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
      osc.start(now);
      osc.stop(now + 0.12);
    } else if (type === 'error') {
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(300, now);
      gain.gain.setValueAtTime(0.06, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
      osc.start(now);
      osc.stop(now + 0.25);
    }
    setTimeout(() => ctx.close(), 300);
  } catch {
    // ignore
  }
}

export function useConversationVoiceRecording({
  isVoiceMode,
  isTurnActive,
}: UseConversationVoiceRecordingOptions): UseConversationVoiceRecordingReturn {
  // Core recording state
  const [mediaRecorder, setMediaRecorder] = createSignal<MediaRecorder | null>(null);
  const [audioChunks, setAudioChunks] = createSignal<Blob[]>([]);
  const [isRecording, setIsRecording] = createSignal(false);
  const [currentMimeType, setCurrentMimeType] = createSignal<string | null>(null);
  const [audioContext, setAudioContext] = createSignal<AudioContext | null>(null);
  const [analyser, setAnalyser] = createSignal<AnalyserNode | null>(null);
  const [mediaStream, setMediaStream] = createSignal<MediaStream | null>(null);
  const [silenceTimeout, setSilenceTimeout] = createSignal<number | null>(null);
  const [recordingTimeoutId, setRecordingTimeoutId] = createSignal<number | null>(null);
  const [recordingStartTime, setRecordingStartTime] = createSignal<number>(0);
  const [loudFrameCount, setLoudFrameCount] = createSignal(0);
  const [silenceAfterLoud, setSilenceAfterLoud] = createSignal(false);
  const [monitorIntervalId, setMonitorIntervalId] = createSignal<number | null>(null);

  // Owns the silence decision and the adaptive noise floor. Reset at the start
  // of every recording so one turn's calibration never leaks into the next.
  const vad = new VoiceActivityDetector();

  // State machine state
  const [convState, setConvState] = createSignal<ConvVoiceState>('idle');

  // Set when a recording is being thrown away rather than transcribed. A plain
  // variable, not a signal: it is read once by the MediaRecorder onstop callback
  // and drives no rendering, so it has no business being reactive.
  let discardNextCapture = false;

  // Latch for the "submit each capture exactly once" rule. Also a plain
  // variable: read once per capture, drives no rendering.
  let submittedCapture = false;

  // True from the first line of startRecording until it returns or throws. Set
  // synchronously, before any await, because isRecording() does not become true
  // until the very end -- for the whole of the await below the hook looks idle,
  // so a second startRecording() walks straight past an isRecording() guard and
  // opens a second microphone. Plain variable for the same reason as above.
  let startingUp = false;

  // True while a /transcribe request is outstanding. The policy must not resume
  // recording until this clears, otherwise it reopens the mic the moment we set
  // 'transcribing' and catches the tail of the sentence just transcribed.
  const [transcriptionInFlight, setTranscriptionInFlight] = createSignal(false);

  // True from the moment a capture is closed until its fate is decided -- chunks
  // read, blob built, /transcribe awaited. The gap between those two is a real
  // hole: stopRecording() clears isRecording() synchronously but the MediaRecorder
  // fires onstop asynchronously, so the policy would reopen the mic in between and
  // the new capture's setAudioChunks([]) could wipe the blob about to be sent.
  const [settling, setSettling] = createSignal(false);

  // Set when opening the microphone fails, cleared by a single retry below.
  // Retrying on every policy tick is a hot loop that re-prompts forever, so one
  // failure parks the hook instead of spinning.
  const [captureBlocked, setCaptureBlocked] = createSignal(false);
  let captureRetryTimer: number | null = null;

  // Bumped when a capture was opened and then handed straight back, so the policy
  // re-decides. Needed because the reason for the drop may be gone by the time we
  // notice: the agent can start and finish an answer entirely inside the await, in
  // which case no signal the policy reads has changed and the mic would simply
  // stay shut with nothing pending.
  const [redecide, setRedecide] = createSignal(0);

  // Consecutive drops. Bounded so a condition that keeps the capture from starting
  // -- the platform reporting speech our own bookkeeping has not seen -- parks the
  // hook instead of retrying as fast as getUserMedia can resolve.
  let consecutiveDrops = 0;

  // Track timers for cleanup
  const [pendingTimeouts, setPendingTimeouts] = createSignal<Set<number>>(new Set());

  function addTimeout(id: number) {
    setPendingTimeouts(prev => new Set(prev).add(id));
  }

  // The whole capture policy, as one rule instead of a transition table.
  //
  // The mic is open when voice mode is on and the agent is not speaking, and at
  // no other time. Everything else follows from those preconditions:
  //
  //   !voiceMode          -> shut
  //   agent speaking      -> shut, and drop what was captured
  //   transcription open  -> shut, so the mic does not catch the user's tail
  //   otherwise           -> open
  //
  // The previous four-state machine (idle/recording/transcribing/paused_tts)
  // existed to remember "voice mode is on but we must not record", which is not
  // a state at all -- it is the conjunction above. Reading it directly removes
  // the possibility of the states disagreeing with the condition, which is how
  // the conversation hook ended up transcribing the agent: it had its own copy
  // of the rule, and that copy had no TTS branch until it was caught.
  //
  // An active turn deliberately does NOT close the mic. Shutting it for the
  // duration of a turn made hands-free conversation impossible -- the user could
  // not say anything while the agent worked, which is the entire reason the
  // queue exists. A real attempt produced no /transcribe request at all, because
  // there was never a capture to transcribe.
  createEffect(() => {
    const voiceMode = isVoiceMode();
    const output = isOutputActive();
    const inFlight = transcriptionInFlight();
    const recording = isRecording();
    const blocked = captureBlocked();
    // Read only so the trace shows it. A turn is not part of this policy; see
    // the note above.
    const turnActive = isTurnActive();
    redecide();

    console.log('[convVoice] policy', { voiceMode, output, inFlight, recording, blocked, turnActive });

    if (!voiceMode) {
      if (recording) stopRecording();
      setConvState('idle');
      return;
    }

    // The agent started talking. Whatever the mic holds now is mostly the
    // agent's own voice, so drop it rather than transcribe it.
    if (output) {
      if (recording) discardRecording();
      setConvState('idle');
      return;
    }

    // A capture is still being read or a /transcribe is outstanding. Reopening
    // now would both clobber the pending chunks and catch the tail of the user's
    // own sentence.
    if (inFlight || settling()) {
      setConvState('transcribing');
      return;
    }

    // getUserMedia failed (no device, permission denied). Retrying on every
    // tick is a hot loop that re-prompts for the microphone forever, so stay
    // shut until the one-shot retry below re-arms.
    if (blocked) {
      setConvState('idle');
      return;
    }

    if (recording) {
      setConvState('recording');
      return;
    }

    setConvState('recording');
    void startRecording();
  });

  // Shut the mic and re-arm once. Without this the policy effect asks for the
  // microphone again on its very next tick, so a persistent failure -- no input
  // device, permission denied -- spins as fast as the event loop turns and
  // re-prompts forever. Verified: an unwired stream made this loop unbounded and
  // exhausted 4GB in 145 seconds.
  function parkCapture() {
    setCaptureBlocked(true);
    if (captureRetryTimer !== null) return;
    captureRetryTimer = window.setTimeout(() => {
      captureRetryTimer = null;
      setCaptureBlocked(false);
    }, CAPTURE_RETRY_MS);
    addTimeout(captureRetryTimer);
  }

  function checkAudioLevels() {
    const reading = vad.read(analyser());
    if (!reading) return;

    const elapsed = Date.now() - recordingStartTime();
    const metMinDuration = elapsed >= MIN_RECORDING_MS;

    if (reading.speech) {
      setLoudFrameCount(loudFrameCount() + 1);
      setSilenceAfterLoud(false);
      const st = silenceTimeout();
      if (st) {
        clearTimeout(st);
        setSilenceTimeout(null);
      }
    } else {
      if (!silenceAfterLoud() && loudFrameCount() > 0) {
        setSilenceAfterLoud(true);
      }
      if (!silenceTimeout() && metMinDuration && loudFrameCount() >= MIN_AUDIO_FRAMES) {
        const st = window.setTimeout(() => {
          if (isRecording()) {
            console.log('[convVoice] silence timeout fired — stopping');
            stopRecording();
          }
        }, SILENCE_DURATION);
        setSilenceTimeout(st);
        addTimeout(st);
      }
    }
  }

  function startAudioMonitor() {
    stopAudioMonitor();
    const id = window.setInterval(checkAudioLevels, MONITOR_INTERVAL_MS);
    setMonitorIntervalId(id);
  }

  function stopAudioMonitor() {
    const id = monitorIntervalId();
    if (id !== null) {
      clearInterval(id);
      setMonitorIntervalId(null);
    }
  }

  async function startRecording() {
    console.log('[convVoice] startRecording called');
    // isRecording() is not a sufficient guard. It only becomes true at the very
    // end of this function, so for the whole of the awaits below the hook looked
    // idle while a capture was being opened: a second request sailed straight
    // past the guard and opened a second microphone, sending identical audio
    // twice. This flag is set synchronously, before any suspension point.
    if (isRecording() || startingUp) return;
    startingUp = true;

    // Half-duplex has to survive the awaits below, not merely be intended.
    // Opening a microphone takes long enough -- a permission prompt, on a cold
    // AudioContext -- for the agent to start answering. Snapshot the speaker
    // before suspending and re-check after: a boolean is stale the moment we
    // yield, whereas a changed generation proves the speaker moved.
    const spokeFor = outputGeneration();
    let stream: MediaStream | null = null;
    let started = false;
    try {
      if (audioContext()) {
        await audioContext().close().catch(() => {});
      }

      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      console.log('[convVoice] got media stream', stream.getTracks());

      // The mic was granted, but the world moved on while we waited. Starting
      // now would put an open microphone in a room where the agent is talking.
      if (!isVoiceMode() || isOutputActiveNow() || outputGeneration() !== spokeFor) {
        console.log('[convVoice] dropping capture — voice mode ended or the agent started speaking while the mic was opening');
        consecutiveDrops += 1;
        return;
      }
      consecutiveDrops = 0;

      setMediaStream(stream);

      const AudioContextCtor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const newAudioContext = new AudioContextCtor();
      setAudioContext(newAudioContext);

      const newAnalyser = newAudioContext.createAnalyser();
      newAnalyser.fftSize = 256;
      setAnalyser(newAnalyser);

      const source = newAudioContext.createMediaStreamSource(stream);
      source.connect(newAnalyser);

      const mimeType =
        MediaRecorder.isTypeSupported('audio/ogg') ? 'audio/ogg' :
        MediaRecorder.isTypeSupported('audio/wav') ? 'audio/wav' :
        MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' :
        MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' :
        null;

      if (!mimeType) {
        console.error('Audio recording not supported in this browser');
        setConvState('idle');
        return;
      }
      console.log('[convVoice] selected mimeType:', mimeType);

      const mr = new MediaRecorder(stream, { mimeType });
      setMediaRecorder(mr);
      setAudioChunks([]);
      setCurrentMimeType(mimeType);

      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          setAudioChunks(prev => [...prev, e.data]);
        }
      };

      mr.onstop = async () => {
        await handleRecordingStop();
      };

      mr.onerror = (e) => {
        console.error('MediaRecorder error:', e);
        cleanup();
        // Same backoff as a failed open: park, then let the policy retry. A
        // MediaRecorder that just faulted will not have been fixed by
        // reopening the microphone immediately.
        parkCapture();
      };

      mr.start();
      started = true;
      setIsRecording(true);
      setRecordingStartTime(Date.now());
      setLoudFrameCount(0);
      setSilenceAfterLoud(false);
      vad.reset();
      // New capture, new submission allowance.
      submittedCapture = false;

      startAudioMonitor();

      const timeoutId = window.setTimeout(() => {
        if (isRecording()) stopRecording();
      }, MAX_RECORDING_MS);
      setRecordingTimeoutId(timeoutId);
      addTimeout(timeoutId);

    } catch (e) {
      console.warn('[convVoice] Failed to start recording:', e);
      setConvState('idle');
      parkCapture();
    } finally {
      startingUp = false;
      // A microphone we were granted but did not use must not be left open --
      // the recording indicator would stay lit with nothing running.
      if (stream && !started) {
        stream.getTracks().forEach(t => t.stop());
        // Hand the decision back. Two drops in a row means the world is not
        // settling, so park rather than retry as fast as getUserMedia resolves.
        if (consecutiveDrops >= 2) {
          consecutiveDrops = 0;
          parkCapture();
        } else {
          setRedecide(n => n + 1);
        }
      }
    }
  }

  function stopRecording() {
    if (!isRecording() || !mediaRecorder()) return;
    const st = silenceTimeout();
    if (st) {
      clearTimeout(st);
      setSilenceTimeout(null);
    }
    const rt = recordingTimeoutId();
    if (rt) {
      clearTimeout(rt);
      setRecordingTimeoutId(null);
    }
    stopAudioMonitor();
    const mr = mediaRecorder();
    // One atomic teardown, and `settling` goes up *inside* the batch. Solid
    // flushes effects after each individual signal write, so clearing
    // isRecording first would let the policy run against a half-closed hook --
    // a closed mic with nothing yet holding the gate -- and open a second
    // capture on top of the one being torn down. That is the duplicate-mic
    // mechanism, in a second disguise.
    batch(() => {
      setSettling(true);
      setMediaRecorder(null);
      setIsRecording(false);
      setSilenceAfterLoud(false);
    });
    try {
      mr.stop();
    } catch (e) {
      console.warn('Error stopping recorder:', e);
      // onstop will never fire, so nothing else would ever clear this.
      setSettling(false);
    }
    const ms = mediaStream();
    if (ms) {
      ms.getTracks().forEach(t => t.stop());
      setMediaStream(null);
    }
    const ctx = audioContext();
    if (ctx) {
      ctx.close().catch(() => {});
      setAudioContext(null);
      setAnalyser(null);
    }
  }

  // Close the mic without transcribing what it captured. Used when the agent
  // starts speaking mid-recording: the tail of that recording is the agent's own
  // voice, and sending it to /transcribe would enqueue the agent's speech as a
  // user turn. The policy effect reopens the mic once output stops.
  function discardRecording() {
    if (!isRecording()) return;
    discardNextCapture = true;
    stopRecording();
  }

  async function handleRecordingStop() {
    try {
      if (discardNextCapture) {
        // Cleared here rather than in discardRecording so it cannot leak into a
        // later, legitimate capture.
        discardNextCapture = false;
        setAudioChunks([]);
        return;
      }
      const elapsed = Date.now() - recordingStartTime();
      const currentLoudFrames = loudFrameCount();

      if (audioChunks().length === 0) {
        handleDiscard("Didn't catch that");
        return;
      }

      const tooShort = elapsed < MIN_RECORDING_MS;
      const notEnoughLoud = currentLoudFrames < MIN_AUDIO_FRAMES;

      if (tooShort || notEnoughLoud) {
        handleDiscard("Didn't catch that");
        return;
      }

      const blob = new Blob(audioChunks(), { type: currentMimeType() || 'audio/webm' });
      setAudioChunks([]);

      // A capture may be submitted to /transcribe at most once. The backend log
      // showed a single blob POSTed twice one millisecond apart -- byte-identical
      // size, identical duration, identical transcript -- so whatever re-entered
      // this path, the second submission was pure loss: two copies of the same
      // sentence queued, and the agent answering it twice.
      if (submittedCapture) return;
      submittedCapture = true;

      console.log('[convVoice] sending blob size:', blob.size, 'mime:', currentMimeType());

      await sendForTranscription(blob);

    } catch (err) {
      console.error('[convVoice] Recording stop error:', err);
      handleDiscard('Processing error');
    } finally {
      // Every exit from stopRecording's shadow passes through here, including the
      // discard and the error paths, so the gate cannot be left shut forever.
      setSettling(false);
    }
  }

  function handleDiscard(reason: string) {
    console.log('[convVoice] Discarded:', reason);
    // No explicit restart. The finally above clears `settling`, which is what
    // was holding the gate; the policy effect reopens the mic. The old code
    // scheduled its own restart from here, which raced the policy and was
    // sometimes a no-op and sometimes a second microphone.
  }

  async function sendForTranscription(blob: Blob) {
    if (!isVoiceMode()) return;

    // These two writes must be atomic. Solid flushes effects after each
    // individual signal write, so without batch() the state machine would
    // observe 'transcribing' with inFlight still false and reopen the mic.
    batch(() => {
      setTranscriptionInFlight(true);
      setConvState('transcribing');
    });
    playEarcon('stop');

    try {
      const mimeExt = (currentMimeType() || 'audio/webm').split('/')[1];
      const formData = new FormData();
      formData.append('file', blob, `audio.${mimeExt}`);

      const res = await fetch('/transcribe', {
        method: 'POST',
        credentials: 'include',
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Transcription failed' }));
        throw new Error(err.detail || 'Transcription failed');
      }

      const data = await res.json();
      const text = (data.text || '').trim();

      if (!text) {
        handleDiscard("Didn't catch that");
        return;
      }

      // Check for exit commands
      if (isExitCommand(text)) {
        // User said "stop listening" - exit voice mode so the state machine
        // stops reopening the mic.
        console.log('[convVoice] Exit command detected:', text);
        exitVoiceMode();
        return;
      }

      // Enqueue transcribed text to message queue
      enqueue({
        content: text,
        source: 'voice',
        timestamp: Date.now(),
      });

      // Trigger the drain. The drainer owns the `processing` flag itself, so we
      // must NOT set it here: drainQueueIfReady() bails out early when
      // isProcessing() is true, which would strand the message in the queue.
      if (!isProcessing()) {
        void triggerDrain();
      }

    } catch (err) {
      console.error('Transcription error:', err);
      if (isVoiceMode()) {
        handleDiscard('Transcription failed');
      }
    } finally {
      // Release the mic gate; the state machine may now resume recording.
      setTranscriptionInFlight(false);
    }
  }

  function isExitCommand(text: string): boolean {
    const EXIT_COMMANDS = ['stop listening', 'exit voice mode', 'stop voice mode', 'goodbye', 'bye'];
    const t = text.toLowerCase().trim();
    return EXIT_COMMANDS.includes(t);
  }

  function cleanup() {
    stopAudioMonitor();

    if (mediaStream()) {
      mediaStream().getTracks().forEach(t => t.stop());
      setMediaStream(null);
    }

    if (audioContext()) {
      audioContext().close().catch(() => {});
      setAudioContext(null);
    }

    setAnalyser(null);
    setMediaRecorder(null);
    setAudioChunks([]);

    const st = silenceTimeout();
    if (st) {
      clearTimeout(st);
      setSilenceTimeout(null);
    }
    const rt = recordingTimeoutId();
    if (rt) {
      clearTimeout(rt);
      setRecordingTimeoutId(null);
    }

    pendingTimeouts().forEach(id => clearTimeout(id));
    setPendingTimeouts(new Set());
  }

  // Cleanup on unmount
  onCleanup(() => {
    cleanup();
  });

  return {
    isRecording,
    state: convState,
  };
}