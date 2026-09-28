import { useVoiceRecording } from '../../hooks/useVoiceRecording'
import { startDictation } from '../../state/voice'
import { createMemo, Show } from 'solid-js'

interface VoiceControlsProps {
  isOpen?: boolean
  isRecording?: boolean
  onStartRecording?: () => void
  onStopRecording?: () => void
  onTranscription?: (text: string) => void
}

export default function VoiceControls(props: VoiceControlsProps) {
  // Memos rather than plain consts. `const isVoiceMode = props.isOpen === true`
  // reads the prop once, so the mode could not follow a change to `isOpen` --
  // and every derived value below hangs off these two.
  const isVoiceMode = createMemo(() => props.isOpen === true)
  const isDictationMode = createMemo(() => !isVoiceMode())

  const { isRecording: hookIsRecording, startRecording, stopRecording } = useVoiceRecording({
    isVoiceMode,
    isDictationMode,
    // Defer the prop read to call time rather than capturing the function.
    onTranscription: (text: string) => props.onTranscription?.(text),
  })

  const isRecording = createMemo(() => props.isRecording ?? hookIsRecording())

  const handleClick = () => {
    if (isRecording()) {
      props.onStopRecording?.()
      stopRecording()
    } else if (isVoiceMode()) {
      props.onStartRecording?.()
      startRecording()
    } else {
      props.onStartRecording?.()
      startDictation()
      startRecording()
    }
  }

  return (
    <Show when={isVoiceMode() || isDictationMode()}>
      <button 
        type="button"
        class="mic-btn btn-icon"
        id="mic-btn"
        title={isRecording() 
          ? (isVoiceMode() ? 'Stop listening' : 'Stop dictation') 
          : (isVoiceMode() ? 'Voice conversation mode (click to listen now)' : 'Dictate into message box')}
        onClick={handleClick}
        classList={{
          recording: isRecording(),
        }}
      >
        {isRecording() ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="6" y="4" width="4" height="16" rx="1"/>
            <rect x="14" y="4" width="4" height="16" rx="1"/>
          </svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            <line x1="12" x2="12" y1="19" y2="22"/>
          </svg>
        )}
      </button>
    </Show>
  )
}