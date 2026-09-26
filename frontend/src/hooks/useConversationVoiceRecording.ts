import { createSignal, createEffect, onCleanup, batch } from 'solid-js';
import { enqueue, isProcessing } from '../state/messageQueue';
import { triggerDrain } from '../services/queueDrainer';
import { VoiceActivityDetector } from '../services/voiceActivity';
import { settings } from '../state/settings';
import { exitVoiceMode } from '../state/voice';

const SILENCE_DURATION = 2000;
const MIN_RECORDING_MS = 500;
const MIN_AUDIO_FRAMES = 3;
const MAX_RECORDING_MS = 180000;
const MONITOR_INTERVAL_MS = 80;

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

  // True while a /transcribe request is outstanding. The state machine must
  // not resume recording until this clears, otherwise it reopens the mic the
  // moment we set 'transcribing' (turnActive is still false at that point).
  const [transcriptionInFlight, setTranscriptionInFlight] = createSignal(false);

  // Track timers for cleanup
  const [pendingTimeouts, setPendingTimeouts] = createSignal<Set<number>>(new Set());

  function addTimeout(id: number) {
    setPendingTimeouts(prev => new Set(prev).add(id));
  }

  // State machine - simplified: idle -> recording -> transcribing -> idle
  createEffect(() => {
    const voiceMode = isVoiceMode();
    const turnActive = isTurnActive();
    const state = convState();
    const inFlight = transcriptionInFlight();

    console.log('[convVoice] state machine tick', { state, voiceMode, turnActive, inFlight });

    switch (state) {
      case 'idle':
        if (voiceMode && !turnActive) {
          console.log('[convVoice] idle -> starting recording');
          setConvState('recording');
          startRecording();
        }
        break;

      case 'recording':
        if (!voiceMode || turnActive) {
          console.log('[convVoice] recording -> idle (condition lost)');
          setConvState('idle');
          stopRecording();
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
        } else if (!turnActive) {
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
            if (isVoiceMode() && !isTurnActive()) {
              setConvState('recording');
              startRecording();
            }
          }, 1000);
        }
      };

      mr.start();
      setIsRecording(true);
      setRecordingStartTime(Date.now());
      setLoudFrameCount(0);
      setSilenceAfterLoud(false);
      vad.reset();

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

  async function handleRecordingStop() {
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
      console.log('[convVoice] sending blob size:', blob.size, 'mime:', currentMimeType());

      await sendForTranscription(blob);

    } catch (err) {
      console.error('[convVoice] Recording stop error:', err);
      handleDiscard('Processing error');
    }
  }

  function handleDiscard(reason: string) {
    console.log('[convVoice] Discarded:', reason);
    if (isVoiceMode() && !isTurnActive()) {
      // Restart listening after a brief pause
      setTimeout(() => {
        if (isVoiceMode() && !isTurnActive()) {
          setConvState('recording');
          startRecording();
        }
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