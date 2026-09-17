import { useVoiceRecording } from '../../hooks/useVoiceRecording'
import { startDictation } from '../../state/voice'
import { Show } from 'solid-js'

interface VoiceControlsProps {
  isOpen?: boolean
  isRecording?: boolean
  onStartRecording?: () => void
  onStopRecording?: () => void
  onTranscription?: (text: string) => void
}

export default function VoiceControls(props: VoiceControlsProps) {
  const isVoiceMode = props.isOpen === true
  const isDictationMode = !isVoiceMode

  const { isRecording: hookIsRecording, startRecording, stopRecording } = useVoiceRecording({
    isVoiceMode,
    isDictationMode,
    onTranscription: props.onTranscription || (() => {}),
  })

  const isRecording = props.isRecording ?? hookIsRecording()

  const handleClick = () => {
    if (isRecording) {
      props.onStopRecording?.()
      stopRecording()
    } else if (isVoiceMode) {
      props.onStartRecording?.()
      startRecording()
    } else {
      props.onStartRecording?.()
      startDictation()
      startRecording()
    }
  }

  const recording = isRecording

  return (
    <Show when={isVoiceMode || isDictationMode}>
      <button 
        type="button"
        class="mic-btn btn-icon"
        id="mic-btn"
        title={recording 
          ? (isVoiceMode ? 'Stop listening' : 'Stop dictation') 
          : (isVoiceMode ? 'Voice conversation mode (click to listen now)' : 'Dictate into message box')}
        onClick={handleClick}
        classList={{
          recording,
        }}
      >
        {recording ? (
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