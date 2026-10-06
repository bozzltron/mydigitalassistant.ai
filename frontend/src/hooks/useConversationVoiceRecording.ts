import { createSignal, createEffect, onCleanup } from 'solid-js';
import { debug } from '../services/logger';
import { enqueue, isProcessing } from '../state/messageQueue';
import { triggerDrain } from '../services/queueDrainer';
import { isExitCommand, playEarcon } from '../services/audio';
import { exitVoiceMode, isOutputActive, startListening, startProcessing } from '../state/voice';
import { createVoiceCapture } from './voiceCapture';

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

export function useConversationVoiceRecording({
  isVoiceMode,
  isTurnActive,
}: UseConversationVoiceRecordingOptions): UseConversationVoiceRecordingReturn {
  const [convState, setConvState] = createSignal<ConvVoiceState>('idle');

  // Captured utterances waiting to be transcribed. A /transcribe round trip must
  // not shut the microphone -- doing so dropped anything said between utterances,
  // which is the whole failure this queue exists to fix. Captures are queued here
  // and sent one at a time, so the transcript order matches the order spoken.
  const pendingSends: Array<{ blob: Blob; mime: string }> = [];
  let drainingSends = false;

  // True while a /transcribe send is outstanding. Drives the *status* only: the
  // mic stays open through the request (see the capture policy below), so the bar
  // must read "Transcribing..." rather than "Listening..." while a request is out.
  const [transcriptionInFlight, setTranscriptionInFlight] = createSignal(false);

  function queueSend(blob: Blob, mime: string) {
    pendingSends.push({ blob, mime });
    void drainSends();
  }

  async function drainSends() {
    if (drainingSends) return;
    drainingSends = true;
    setTranscriptionInFlight(true);
    try {
      while (pendingSends.length > 0) {
        const next = pendingSends.shift()!;
        await sendForTranscription(next.blob, next.mime);
      }
    } finally {
      drainingSends = false;
      setTranscriptionInFlight(false);
    }
  }

  // Set when opening the microphone fails, cleared by a single retry below.
  // Retrying on every policy tick is a hot loop that re-prompts forever, so one
  // failure parks the hook instead of spinning.
  const [captureBlocked, setCaptureBlocked] = createSignal(false);
  let captureRetryTimer: number | null = null;

  // Consecutive drops. Bounded so a condition that keeps the capture from
  // starting -- the platform reporting speech our own bookkeeping has not seen --
  // parks the hook instead of retrying as fast as getUserMedia can resolve.
  let consecutiveDrops = 0;

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
  }

  const capture = createVoiceCapture({
    label: '[convVoice]',
    shouldCapture: () => isVoiceMode(),
    onCapture: (blob, mime) => queueSend(blob, mime),
    onDiscard: (reason) => debug('[convVoice] discarded:', reason),
    onStarted: () => {
      consecutiveDrops = 0;
    },
    onStartError: () => {
      setConvState('idle');
      parkCapture();
    },
    onRecorderError: () => {
      capture.cleanup();
      // Same backoff as a failed open: a MediaRecorder that just faulted will
      // not have been fixed by reopening the microphone immediately.
      parkCapture();
    },
    onStartDropped: () => {
      // Two drops in a row means the world is not settling, so park rather than
      // retry as fast as getUserMedia resolves.
      consecutiveDrops += 1;
      if (consecutiveDrops >= 2) {
        consecutiveDrops = 0;
        parkCapture();
      }
    },
  });

  const isRecording = capture.isRecording;

  // The whole capture policy, as one rule instead of a transition table.
  //
  // The mic is open whenever voice mode is on and the agent is not speaking.
  // Everything else follows from those preconditions:
  //
  //   !voiceMode          -> shut
  //   agent speaking      -> shut, and drop what was captured
  //   recorder settling   -> wait for the blob to be assembled, then reopen
  //   otherwise           -> open
  //
  // A turn in flight and a /transcribe in flight both deliberately leave the mic
  // OPEN. Shutting it for a turn made hands-free conversation impossible; shutting
  // it for the transcription round trip dropped whatever was said between
  // utterances. Captures are queued and sent serially instead (see `pendingSends`),
  // so listening never costs ordering.
  createEffect(() => {
    const voiceMode = isVoiceMode();
    const output = isOutputActive();
    const recording = capture.isRecording();
    const blocked = captureBlocked();
    // True while a /transcribe send is outstanding. Status-only: the mic stays
    // open through the request (see below), but the bar reads "Transcribing...".
    const transcribing = transcriptionInFlight();
    // Read only so the trace shows it. A turn is not part of this policy.
    const turnActive = isTurnActive();
    // Re-decide whenever a capture starts, ends, or is handed back.
    capture.epoch();

    debug('[convVoice] policy', { voiceMode, output, recording, blocked, transcribing, turnActive });

    if (!voiceMode) {
      if (recording) capture.stop();
      setConvState('idle');
      return;
    }

    // The agent started talking. Whatever the mic holds now is mostly the
    // agent's own voice, so drop it rather than transcribe it.
    if (output) {
      if (recording) capture.discard();
      setConvState('idle');
      return;
    }

    // The recorder is closing and its blob is being assembled. This is the only
    // window that blocks capture: a /transcribe in flight does NOT, so speech
    // between utterances is heard instead of dropped.
    if (capture.isSettling()) {
      setConvState('transcribing');
      startProcessing();
      return;
    }

    // getUserMedia failed (no device, permission denied). Stay shut until the
    // one-shot retry above re-arms.
    if (blocked) {
      setConvState('idle');
      return;
    }

    setConvState('recording');
    // The bar shows one string. "Transcribing..." overrides "Listening..." while a
    // /transcribe send is outstanding, even though the mic stays open through it
    // (it opens below and is not shut for the request) -- visually exclusive,
    // functionally both true. The user sees the work, and nothing said is missed.
    if (transcribing) {
      startProcessing();
    } else {
      startListening();
    }

    if (recording) return;
    void capture.start();
  });

  async function sendForTranscription(blob: Blob, mime: string) {
    // No `!isVoiceMode()` guard: the audio was captured while listening, so it is
    // transcribed and queued even if voice mode ended meanwhile. Dropping it here
    // was silent data loss.
    playEarcon('stop');

    try {
      const mimeExt = (mime || 'audio/webm').split('/')[1];
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
        debug('[convVoice] discarded: empty transcript');
        return;
      }

      // Check for exit commands
      if (isExitCommand(text)) {
        // User said "stop listening" - exit voice mode so the policy stops
        // reopening the mic.
        debug('[convVoice] Exit command detected:', text);
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
      debug('[convVoice] discarded: transcription failed');
    }
  }

  // Cleanup on unmount
  onCleanup(() => {
    capture.cleanup();
    if (captureRetryTimer !== null) {
      clearTimeout(captureRetryTimer);
      captureRetryTimer = null;
    }
  });

  return {
    isRecording,
    state: convState,
  };
}
