import { createSignal, createEffect, onCleanup, batch } from 'solid-js';
import { enqueue, isProcessing } from '../state/messageQueue';
import { triggerDrain } from '../services/queueDrainer';
import { VoiceActivityDetector } from '../services/voiceActivity';
import { settings } from '../state/settings';
import { exitVoiceMode, isTtsSpeaking } from '../state/voice';

const SILENCE_DURATION = 2000;
const MIN_RECORDING_MS = 500;
const MIN_AUDIO_FRAMES = 3;
const MAX_RECORDING_MS = 180000;
const MONITOR_INTERVAL_MS = 80;

type ConvVoiceState = 'idle' | 'recording' | 'transcribing' | 'paused_tts';

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

  // True while a /transcribe request is outstanding. The state machine must
  // not resume recording until this clears, otherwise it reopens the mic the
  // moment we set 'transcribing' (turnActive is still false at that point).
  const [transcriptionInFlight, setTranscriptionInFlight] = createSignal(false);

  // Track timers for cleanup
  const [pendingTimeouts, setPendingTimeouts] = createSignal<Set<number>>(new Set());

  function addTimeout(id: number) {
    setPendingTimeouts(prev => new Set(prev).add(id));
  }

  // State machine - idle -> recording -> transcribing -> idle, with a
  // paused_tts detour. TTS is read here, alongside voiceMode/turnActive,
  // because the browser speaking is a third thing that must close the mic.
  createEffect(() => {
    const voiceMode = isVoiceMode();
    const turnActive = isTurnActive();
    const tts = isTtsSpeaking();
    const state = convState();
    const inFlight = transcriptionInFlight();

    console.log('[convVoice] state machine tick', { state, voiceMode, turnActive, tts, inFlight });

    switch (state) {
      case 'idle':
        if (voiceMode && !turnActive) {
          if (tts) {
            console.log('[convVoice] idle -> paused_tts (TTS playing)');
            setConvState('paused_tts');
          } else {
            console.log('[convVoice] idle -> starting recording');
            setConvState('recording');
            startRecording();
          }
        }
        break;

      case 'recording':
        if (!voiceMode || turnActive) {
          console.log('[convVoice] recording -> idle (condition lost)');
          setConvState('idle');
          stopRecording();
        } else if (tts) {
          // The agent started talking over a recording in progress. Close the mic
          // and drop what was captured -- it is mostly the agent's own voice.
          console.log('[convVoice] recording -> paused_tts (TTS started mid-recording)');
          setConvState('paused_tts');
          discardRecording();
        }
        break;

      case 'paused_tts':
        if (!voiceMode || turnActive) {
          console.log('[convVoice] paused_tts -> idle (condition lost)');
          setConvState('idle');
        } else if (!tts) {
          console.log('[convVoice] paused_tts -> recording (TTS ended)');
          setConvState('recording');
          startRecording();
        }
        break;

      case 'transcribing':
        // Transcription in progress - wait for completion
        if (!voiceMode) {
          console.log('[convVoice] transcribing -> idle (voice mode lost)');
          setConvState('idle');
        } else if (inFlight) {
          // Request still outstanding, hold the mic closed
          console.log('[convVoice] transcribing -> waiting on /transcribe');
        } else if (turnActive) {
          // The drainer sent this turn the moment it was enqueued, so the
          // response is already streaming. Wait for it rather than reopening
          // the mic underneath the agent.
          console.log('[convVoice] transcribing -> waiting for turn to finish');
        } else if (tts) {
          console.log('[convVoice] transcribing -> paused_tts (TTS playing)');
          setConvState('paused_tts');
        } else {
          // Transcription done, ready to resume listening
          console.log('[convVoice] transcribing -> recording (turn complete, resuming)');
          setConvState('recording');
          startRecording();
        }
        break;
    }
  });

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
    if (isRecording()) return;
    try {
      if (audioContext()) {
        await audioContext().close().catch(() => {});
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      console.log('[convVoice] got media stream', stream.getTracks());
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
        stream.getTracks().forEach(t => t.stop());
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
        if (convState() !== 'idle') {
          setConvState('idle');
          cleanup();
          // Restart after error
          setTimeout(() => {
            restartRecording();
          }, 1000);
        }
      };

      mr.start();
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
    setMediaRecorder(null);
    setIsRecording(false);
    setSilenceAfterLoud(false);
    try {
      mr.stop();
    } catch (e) {
      console.warn('Error stopping recorder:', e);
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

  // Close the mic without transcribing what it captured. Used when TTS starts
  // mid-recording: the tail of that recording is the agent's own voice, and
  // sending it to /transcribe would enqueue the agent's speech as a user turn.
  // The state machine reopens the mic when TTS ends.
  function discardRecording() {
    if (!isRecording()) return;
    discardNextCapture = true;
    stopRecording();
  }

  async function handleRecordingStop() {
    if (discardNextCapture) {
      // Cleared here rather than in discardRecording so it cannot leak into a
      // later, legitimate capture.
      discardNextCapture = false;
      setAudioChunks([]);
      return;
    }
    const elapsed = Date.now() - recordingStartTime();
    const currentLoudFrames = loudFrameCount();

    try {
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
    }
  }

  // Shared by the error-restart and discard-restart paths. Both used to reopen
  // the mic directly, bypassing the state machine and with no TTS check -- which
  // is how a capture could start while the agent was speaking. Going through
  // paused_tts hands the resume back to the machine.
  function restartRecording() {
    if (!isVoiceMode() || isTurnActive()) return;
    if (isTtsSpeaking()) {
      setConvState('paused_tts');
      return;
    }
    setConvState('recording');
    startRecording();
  }

  function handleDiscard(reason: string) {
    console.log('[convVoice] Discarded:', reason);
    if (isVoiceMode() && !isTurnActive()) {
      // Restart listening after a brief pause
      setTimeout(() => {
        restartRecording();
      }, 500);
    }
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