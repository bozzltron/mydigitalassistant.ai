import { createSignal, createEffect, onCleanup, batch } from 'solid-js';
import { debug } from '../services/logger';
import { enqueue, isProcessing } from '../state/messageQueue';
import { triggerDrain } from '../services/queueDrainer';
import { isExitCommand, playEarcon } from '../services/audio';
import { exitVoiceMode, isOutputActive } from '../state/voice';
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

  // True while a /transcribe request is outstanding. The policy must not resume
  // recording until this clears, otherwise it reopens the mic the moment we set
  // 'transcribing' and catches the tail of the sentence just transcribed.
  const [transcriptionInFlight, setTranscriptionInFlight] = createSignal(false);

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
    onCapture: (blob, mime) => sendForTranscription(blob, mime),
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
  // The mic is open when voice mode is on and the agent is not speaking, and at
  // no other time. Everything else follows from those preconditions:
  //
  //   !voiceMode          -> shut
  //   agent speaking      -> shut, and drop what was captured
  //   transcription open  -> shut, so the mic does not catch the user's tail
  //   otherwise           -> open
  //
  // An active turn deliberately does NOT close the mic. Shutting it for the
  // duration of a turn made hands-free conversation impossible -- the user could
  // not say anything while the agent worked, which is the entire reason the
  // queue exists.
  createEffect(() => {
    const voiceMode = isVoiceMode();
    const output = isOutputActive();
    const inFlight = transcriptionInFlight();
    const recording = capture.isRecording();
    const blocked = captureBlocked();
    // Read only so the trace shows it. A turn is not part of this policy.
    const turnActive = isTurnActive();
    // Re-decide whenever a capture starts, ends, or is handed back.
    capture.epoch();

    debug('[convVoice] policy', { voiceMode, output, inFlight, recording, blocked, turnActive });

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

    // A capture is still being read or a /transcribe is outstanding. Reopening
    // now would both clobber the pending chunks and catch the tail of the user's
    // own sentence.
    if (inFlight || capture.isSettling()) {
      setConvState('transcribing');
      return;
    }

    // getUserMedia failed (no device, permission denied). Stay shut until the
    // one-shot retry above re-arms.
    if (blocked) {
      setConvState('idle');
      return;
    }

    if (recording) {
      setConvState('recording');
      return;
    }

    setConvState('recording');
    void capture.start();
  });

  async function sendForTranscription(blob: Blob, mime: string) {
    if (!isVoiceMode()) return;

    // These two writes must be atomic. Solid flushes effects after each
    // individual signal write, so without batch() the policy would observe
    // 'transcribing' with inFlight still false and reopen the mic.
    batch(() => {
      setTranscriptionInFlight(true);
      setConvState('transcribing');
    });
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
      if (isVoiceMode()) {
        debug('[convVoice] discarded: transcription failed');
      }
    } finally {
      // Release the mic gate; the policy may now resume recording.
      setTranscriptionInFlight(false);
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
