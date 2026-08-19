# Voice Mode Redesign Plan

## 1. Problem Statement

The current voice conversation mode in `assistant/backend/static/chat.html` is unreliable for hands-free use. After the first user utterance and assistant response, the microphone appears to stop listening because the UI state machine has multiple conflicting restart paths and no clear visual feedback. The user cannot interrupt the assistant, cannot see whether the system is listening, and the conversation quickly breaks down.

### Current bugs identified in code

- **Race condition on restart:** `sendMessage()` calls `startListeningForVoice()` immediately after the response, while `speak()` installs a TTS `onend` handler that also restarts listening. The two paths cancel each other.
- **Status text is never reset:** `sendAudioForTranscription()` sets the overlay text to "Transcribing...", but `startRecording()` never resets it to "Listening", so the user thinks the mic is off.
- **TTS is cancelled immediately:** `startListeningForVoice()` calls `stopSpeaking()`, cutting off the assistant's reply before it is heard.
- **No barge-in:** while the assistant speaks, the microphone is off. The user cannot interrupt.
- **No audio cues:** there are no earcons (beeps) for listen-start, listen-stop, or error states.
- **Stop button exits the whole mode:** the overlay "Stop" button calls `exitVoiceMode()`, so the natural gesture after speaking once ends the session.
- **State is not restored on reload:** `voiceMode` is persisted to `localStorage` but `init()` does not re-enter voice mode after a page refresh.

## 2. Vision

Voice mode should feel like a natural, hands-free conversation:

1. The user clicks one button (or uses a wake phrase in the future) and can then talk continuously.
2. The system clearly signals when it is listening, thinking, and speaking.
3. The assistant finishes speaking, then automatically listens again.
4. The user can interrupt the assistant at any time by speaking.
5. Transcription errors, network errors, and empty utterances are recovered gracefully without killing the session.
6. The user can exit voice mode explicitly with a single gesture or voice command.

## 3. User Scenarios

### Scenario A: Cooking assistance
> User is cooking with messy hands. They ask "How many grams of flour in two cups?" The assistant answers. They immediately ask "And sugar?" The assistant answers. They say "Thanks" and later "Actually, make that three cups." The assistant tracks the updated quantity.

Requirements: continuous listening, barge-in, context across turns, no touch required.

### Scenario B: Walking and brainstorming
> User is walking and thinking aloud about a project. They say "I think we should use PostgreSQL, not SQLite." The assistant confirms and stores the decision. They continue "And we need to add user auth by Friday." The assistant adds a goal with a deadline. They interrupt the assistant's confirmation with "Wait, use SQLite for now, keep it simple." The assistant updates the decision.

Requirements: hands-free, long-running session, correction handling, memory updates.

### Scenario C: Recovering from a mistake
> User says something, assistant transcribes it as nonsense. User says "No, that's wrong" or "Cancel that." The assistant discards the bad turn and re-listens without requiring a button press.

Requirements: robust error recovery, explicit cancellation utterances.

## 4. Functional Requirements

### 4.1 Voice states

The system must be in exactly one of these states at any time during voice mode:

| State | Meaning | Visual | Audio |
|---|---|---|---|
| `IDLE` | Voice mode off | Normal chat UI | None |
| `LISTENING` | Recording user audio | Overlay visible, pulsing ring, status "Listening" | Soft start beep |
| `PROCESSING` | Audio captured, transcribing / waiting for backend | Overlay visible, status "Thinking" / "Transcribing" | Soft stop beep |
| `SPEAKING` | Assistant reply is being read aloud | Overlay visible or assistant indicator, status "Speaking" | None (TTS itself) |

State transitions must be guarded by a single `setVoiceState(newState)` function.

### 4.2 State transitions

```
IDLE --(enter voice mode)--> LISTENING
LISTENING --(silence detected)--> PROCESSING
LISTENING --(manual stop / cancel)--> IDLE
LISTENING --(empty transcription)--> LISTENING (after brief delay)
PROCESSING --(transcription + response received)--> SPEAKING
PROCESSING --(transcription error)--> LISTENING (after error message)
SPEAKING --(TTS finished)--> LISTENING
SPEAKING --(user barge-in)--> LISTENING (stop TTS)
SPEAKING --(manual stop)--> IDLE
```

### 4.3 Barge-in

- While in `SPEAKING`, keep a low-threshold audio monitor active (using the same `AudioContext`/analyser pattern).
- If user speech exceeds a barge-in threshold for a short duration, immediately:
  1. Cancel TTS and hide the "Stop speaking" button.
  2. Stop the current utterance.
  3. Transition to `LISTENING`.
- The barge-in threshold should be lower than the silence threshold so that normal speaking volume reliably interrupts.

### 4.4 Audio feedback

- `LISTENING` start: short ascending tone (generated with Web Audio API, no external files).
- `LISTENING` stop / `PROCESSING` start: short descending tone.
- Error / no-speech detected: gentle low tone or no tone (avoid annoyance).
- All tones must be synthesized locally; no network requests.

### 4.5 Status text

- `LISTENING`: "Listening"
- `PROCESSING` during transcription: "Transcribing..."
- `PROCESSING` after transcription, waiting for response: "Thinking..."
- `SPEAKING`: "Speaking"
- Error: brief error text, then return to "Listening"

### 4.6 Exit gestures

- The overlay "Cancel" button exits voice mode and returns to `IDLE`.
- The overlay "Stop" button in the new design should finish the current turn and exit (or be renamed to "Done").
- A spoken phrase like "stop listening" or "exit voice mode" should exit voice mode. This can be detected via heuristic on the transcription text.

### 4.7 Session persistence

- `voiceMode` state is persisted to `localStorage`.
- On page load, if `voiceMode` was active, the UI must re-enter voice mode automatically.
- The session ID must be preserved across reloads.

### 4.8 Text input coexistence

- While in voice mode, the text input remains available as a fallback.
- Sending a text message does not exit voice mode; after the assistant responds, voice mode resumes listening.

## 5. Non-Functional Requirements

### 5.1 Performance

- Time from silence detection to status change to "Thinking": < 200 ms.
- Time from response arrival to TTS start: < 300 ms.
- Barge-in latency (user speaks -> TTS stops): < 300 ms.
- No continuous high-CPU audio monitoring when not in voice mode.

### 5.2 Robustness

- Microphone permission denial must show a clear message and exit voice mode.
- If transcription fails, voice mode must recover automatically.
- If the backend `/chat` request fails, voice mode must report the error and re-listen.
- Empty transcriptions (user said nothing meaningful) must not send a chat request; just re-listen.

### 5.3 Privacy

- Audio is never stored locally or sent anywhere except to the local `/transcribe` endpoint.
- Audio analysis for barge-in happens locally in the browser.

### 5.4 Accessibility

- Voice mode must be keyboard-activatable.
- Status changes should be screen-reader friendly (use `aria-live` region).

## 6. Implementation Design

### 6.1 State machine

Introduce a single source of truth:

```javascript
const VoiceState = {
  IDLE: 'idle',
  LISTENING: 'listening',
  PROCESSING: 'processing',
  SPEAKING: 'speaking',
};

let voiceState = VoiceState.IDLE;

function setVoiceState(newState, reason = '') {
  // guard invalid transitions, update UI, log for debugging
}
```

All transitions go through `setVoiceState`.

### 6.2 Audio monitor

- A single `AudioContext`/analyser is created when recording starts.
- `monitorAudioLevels()` runs only while `voiceState === VoiceState.LISTENING` or `VoiceState.SPEAKING` (for barge-in).
- Use `requestAnimationFrame` for the monitoring loop.

### 6.3 Barge-in implementation

During `SPEAKING`:
- Reuse the `AudioContext` from the listening phase or create a fresh one with `echoCancellation`/`noiseSuppression`.
- Use a lower threshold and shorter duration than silence detection.
- On trigger, call `speechSynthesis.cancel()` and transition to `LISTENING`.
- Use `speakGeneration` to prevent the TTS `onend` handler from restarting listening after a barge-in.

### 6.4 TTS lifecycle

- `speak(text)` increments `speakGeneration`, cancels any current speech, creates the utterance, and transitions to `SPEAKING`.
- The `onstart` event (if available) confirms the transition.
- The `onend`/`onerror` handler checks the generation and only restarts listening if the generation is still current and voice mode is active.

### 6.5 Recovery from empty/error transcription

- If `data.text` is empty after transcription, schedule `startRecording()` after 500 ms and show "Didn't catch that" briefly.
- If the transcription fetch fails, show the error briefly and re-listen after 1 second.

## 7. Testing Plan

### 7.1 Manual tests

1. Enter voice mode, say one thing, verify assistant responds and automatically listens again.
2. Interrupt the assistant mid-sentence by speaking; verify TTS stops and mic reopens.
3. Say nothing; verify the system re-listens after silence without error.
4. Refresh the page while in voice mode; verify voice mode resumes.
5. Click "Cancel"; verify voice mode exits cleanly and mic is released.
6. Type a message while in voice mode; verify voice mode resumes after the response.
7. Deny microphone permission; verify graceful error and exit.

### 7.2 Automated tests

- Unit tests for `setVoiceState` transition matrix.
- Integration tests for the transcription flow using a mocked `MediaRecorder`.
- These are best added as a new test file or as browser-based tests once a JS test harness exists.

## 8. Open Questions

1. Should we support a wake word ("Hey Assistant") to start voice mode without clicking? This is future work; out of scope for this phase.
2. Should voice mode work on mobile browsers with their different audio policies? Target desktop first; mobile as follow-up.
3. Should the assistant read back what it heard for confirmation? For now, no; rely on the transcript appearing in the chat. This can be revisited if mis-transcriptions are common.

## 9. Success Criteria

- A user can hold a 5-turn hands-free conversation without touching the keyboard or mouse.
- Barge-in works reliably in a quiet room.
- The visual status always matches the actual system state.
- No "zombie" states where the mic is open but the overlay says "Transcribing...".
- Refreshing the page does not silently break voice mode.
