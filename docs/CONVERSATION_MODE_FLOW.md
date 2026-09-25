# Conversation Mode: Expected Flow of Operations

> **Purpose**: Single source of truth for the hands-free voice conversation mode behavior.
> Used for implementation, testing, and future maintenance.

---

## User Experience Flow

```mermaid
sequenceDiagram
    participant User
    participant UI
    participant VoiceHook
    participant Backend
    participant TTS

    User->>UI: Clicks "Let's talk"
    UI->>VoiceHook: enterVoiceMode() → status='listening'
    VoiceHook->>VoiceHook: Auto-start recording
    VoiceHook-->>User: 🔊 Earcon "start"
    
    User->>VoiceHook: Speaks...
    VoiceHook->>VoiceHook: Detects silence (2s) OR User clicks "I'm done talking"
    VoiceHook->>VoiceHook: stopRecording() → status='processing'
    VoiceHook-->>User: 🔊 Earcon "stop"
    
    VoiceHook->>Backend: POST /transcribe (audio blob)
    Backend-->>VoiceHook: { text: "..." }
    
    VoiceHook->>VoiceHook: status='listening' (pre-resume)
    VoiceHook->>UI: onTranscription(text)
    UI->>Backend: POST /chat/stream (message)
    
    Note over UI,Backend: Turn active (isTurnActive=true)
    VoiceHook->>VoiceHook: Auto-stop recording (turnActive)
    
    Backend-->>UI: Streaming response...
    UI->>TTS: Speak response (if TTS enabled)
    TTS-->>VoiceHook: isTtsSpeaking=true
    VoiceHook->>VoiceHook: Pause recording
    
    TTS-->>VoiceHook: isTtsSpeaking=false (onend)
    VoiceHook->>VoiceHook: Resume recording (if voiceMode && !turnActive)
    VoiceHook-->>User: 🔊 Earcon "start"
    
    User->>UI: Clicks "Cancel" OR says "stop listening"
    UI->>VoiceHook: exitVoiceMode() → status='idle'
```

---

## State Machine

### Voice Mode States (Internal to `useVoiceRecording`)

```
┌─────────────┐
│    IDLE     │◄──────────────────────────────────┐
└──────┬──────┘                                   │
       │ enterVoiceMode()                          │ exitVoiceMode()
       ▼                                           │
┌─────────────┐     startRecording()              │
│  STARTING   │──────────────────────────────────►│
└──────┬──────┘                                   │
       │ recording started                         │
       ▼                                           │
┌─────────────┐     silence / user stop           │
│  RECORDING  │──────────────────────────────────►│
└──────┬──────┘                                   │
       │                                           │
       │ transcription                             │
       ▼                                           │
┌─────────────┐     transcription done            │
│ PROCESSING  │──────────────────────────────────►│
└──────┬──────┘                                   │
       │                                           │
       │ turnActive=true (message sent)            │
       ▼                                           │
   (blocked - no recording during turn)            │
       │                                           │
       │ turnActive=false, TTS not speaking        │
       ▼                                           │
┌─────────────┐     auto-restart                  │
│  STARTING   │──────────────────────────────────►│
└──────┬──────┘                                   │
       │                                           │
       │ TTS starts speaking                       │
       ▼                                           │
┌─────────────┐     TTS ends                      │
│ PAUSED_TTS  │──────────────────────────────────►│
└──────┬──────┘                                   │
       │                                           │
       │ user clicks "I'm done talking"            │
       ▼                                           │
┌─────────────┐     (stays here until             │
│ USER_STOPPED│      voiceMode=false)             │
└──────┬──────┘                                   │
       │                                           │
       └───────────────────────────────────────────┘
```

### Voice Status (Exposed via `voice.ts` store)

| Status | Meaning | Recording? |
|--------|---------|------------|
| `idle` | Voice mode off | No |
| `listening` | Actively recording / waiting for speech | Yes |
| `processing` | Transcribing audio | No |
| `speaking` | Agent speaking (voice mode TTS) | No |
| `error` | Error state | No |

**Note**: `isTtsSpeaking` is a separate flag for browser SpeechSynthesis TTS.

---

## Key Invariants

### 1. Recording Only When:
- `voiceMode === true` (user clicked "Let's talk")
- `turnActive === false` (not waiting for backend response)
- `isTtsSpeaking === false` (TTS not playing)
- `userInitiatedStop === false` (user hasn't clicked "I'm done talking")

### 2. Auto-Restart Conditions:
After a turn completes (assistant response fully received + TTS finished):
- Voice mode still active (`voiceMode === true`)
- No active turn (`turnActive === false`)
- TTS not speaking (`isTtsSpeaking === false`)
- User didn't explicitly stop (`userInitiatedStop === false`)

### 3. "I'm Done Talking" Button:
- **Always stops current recording immediately**
- **Sets `userInitiatedStop = true`**
- **Prevents auto-restart** until user clicks "Let's talk" again
- During TTS: stops recording, doesn't restart after TTS ends

### 4. TTS Interruption:
- If TTS starts while recording → pause recording
- If TTS ends → resume recording **only if** all auto-restart conditions met
- User can click "Stop" button during TTS to cancel speech + stay in voice mode

---

## Component Responsibilities

### `TopBar.tsx` - Voice Mode Toggle
```typescript
// "Let's talk" button
onClick={() => {
  if (voice.status !== 'idle') {
    exitVoiceMode()
    updateSetting('voiceMode', false)
  } else {
    enterVoiceMode()
    updateSetting('voiceMode', true)
  }
}}

// "I'm done talking" button (inline in status bar)
onClick={() => stopRecording()}  // Calls hook's user-stop handler

// "Cancel" button (exits voice mode entirely)
onClick={() => {
  exitVoiceMode()
  updateSetting('voiceMode', false)
}}
```

### `ChatPage.tsx` - Hook Integration
```typescript
useVoiceRecording({
  isVoiceMode: () => isVoiceModeActive(),  // listening OR processing
  isDictationMode: () => isDictating(),
  isTurnActive: () => isTurnActive(),
  onTranscription: handleSendMessage,  // Sends to chat pipeline
})
```

### `useVoiceRecording.ts` - Core Logic
- MediaRecorder lifecycle
- Silence detection (2s after speech)
- Audio level monitoring (80ms interval)
- Transcription API call
- State machine (see above)
- Earcon sounds

### `voice.ts` - Shared State
- `voice.status` (idle/listening/processing/speaking/error)
- `voice.isDictating` (one-shot dictation mode)
- `voice.isTtsSpeaking` (browser SpeechSynthesis)
- `registerStopRecording` / `stopRecording` (callback for TopBar)

---

## Edge Cases Handled

| Scenario | Behavior |
|----------|----------|
| User clicks "Let's talk" while TTS speaking | TTS cancelled, recording starts after 100ms |
| Silence detected but < 500ms recorded | Discarded, "Didn't catch that", auto-restart |
| Transcription returns empty | Discarded, "Didn't catch that", auto-restart |
| User says "stop listening" | `exitVoiceMode()`, no auto-restart |
| Network error on transcription | Error earcon, retry after 1.2s |
| Browser denies microphone | Error earcon, exit voice mode |
| User switches conversation | Voice mode exits (cleanup in hook) |
| Page unload while recording | Cleanup stops recorder, releases mic |

---

## Timing Constants

```typescript
const SILENCE_DURATION = 2000      // Ms of silence before auto-stop
const MIN_RECORDING_MS = 500       // Minimum recording length
const MIN_AUDIO_LEVEL = 0.015      // Normalized audio level threshold
const MIN_AUDIO_FRAMES = 3         // Min loud frames before silence detection works
const MAX_RECORDING_MS = 180000    // Hard limit (3 minutes)
const MONITOR_INTERVAL_MS = 80     // Audio level check interval
const TTS_RESUME_DELAY = 100       // Ms to wait after TTS ends before restart
const RETRY_DELAY = 1200           // Ms before retry after error/discard
```

---

## Testing Checklist

### Unit Tests (`useVoiceRecording`)
- [ ] Auto-start on `enterVoiceMode()`
- [ ] Stop on `exitVoiceMode()`
- [ ] Stop on `turnActive=true`
- [ ] Pause on TTS start, resume on TTS end
- [ ] Silence detection triggers stop
- [ ] "I'm done talking" sets `userInitiatedStop`, prevents restart
- [ ] Transcription error → retry
- [ ] Empty transcription → discard + retry
- [ ] Exit command ("stop listening") → `exitVoiceMode()`

### Integration Tests (`ChatPage`)
- [ ] Full cycle: speak → transcribe → send → response → TTS → resume
- [ ] Queue messages while turn active
- [ ] Switch conversation exits voice mode
- [ ] Settings: TTS toggle, sound effects toggle

### E2E Tests
- [ ] "Let's talk" → speak → "I'm done talking" → response heard → auto-resume
- [ ] "Let's talk" → TTS plays → click "Stop" (TTS) → voice mode continues
- [ ] "Let's talk" → speak → "Cancel" → voice mode exits

---

## Related Files

| File | Role |
|------|------|
| `frontend/src/hooks/useVoiceRecording.ts` | Core recording/transcription logic |
| `frontend/src/state/voice.ts` | Shared voice state store |
| `frontend/src/components/chat/ChatPage.tsx` | Hook consumer, TTS integration |
| `frontend/src/components/ui/TopBar.tsx` | Voice mode UI controls |
| `frontend/src/components/chat/VoiceControls.tsx` | Dictation mic button (separate from conversation mode) |
| `frontend/src/state/chat.ts` | `isTurnActive` signal |
| `frontend/src/services/api.ts` | `/transcribe` endpoint |

---

## Future Enhancements

1. **VAD (Voice Activity Detection)**: Replace silence timeout with WebRTC VAD or Silero VAD for better accuracy
2. **Barge-in**: Allow user to interrupt TTS by speaking (requires audio ducking/mixing)
3. **Wake word**: "Hey Assistant" to enter voice mode hands-free
4. **Visual feedback**: Real-time audio waveform in status bar
5. **Multi-turn context**: Keep voice mode active across multiple exchanges without "I'm done talking"

---

*Last updated: 2026-09-25*
*Review with any changes to conversation mode behavior*