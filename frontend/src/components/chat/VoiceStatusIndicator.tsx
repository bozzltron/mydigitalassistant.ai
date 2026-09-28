import { createSignal, createMemo, onMount, onCleanup, Show } from 'solid-js'
import { voice, isListening, isProcessing, isSpeaking } from '../../state/voice'

interface VoiceStatusIndicatorProps {
  isVoiceMode: boolean
  isDictationMode: boolean
  isRecording: () => boolean
  onStopRecording: () => void
}

export default function VoiceStatusIndicator(props: VoiceStatusIndicatorProps) {
  const [showOverlay, setShowOverlay] = createSignal(false)

  const status = createMemo(() => {
    const v = voice()
    return {
      isListening: isListening(),
      isProcessing: isProcessing(),
      isSpeaking: isSpeaking(),
      status: v.status,
    }
  })

  onMount(() => {
    const checkRecording = () => {
      if (props.isDictationMode && props.isRecording()) {
        setShowOverlay(true)
      } else {
        setShowOverlay(false)
      }
    }
    const interval = setInterval(checkRecording, 100)
    onCleanup(() => clearInterval(interval))
  })

  const getStatusText = () => {
    const s = status()
    if (props.isDictationMode) {
      if (s.isListening) return 'Dictating...'
      if (s.isProcessing) return 'Processing...'
      return 'Ready'
    }
    if (s.isListening) return 'Listening...'
    if (s.isProcessing) return 'Thinking...'
    if (s.isSpeaking) return 'Speaking...'
    return 'Idle'
  }

  const getDotClass = () => {
    const s = status()
    if (s.isProcessing) return 'voice-dot processing'
    if (s.isSpeaking) return 'voice-dot speaking'
    return 'voice-dot'
  }

  return (
    <Show when={props.isVoiceMode || props.isDictationMode}>
      <Show when={showOverlay()}>
        {/* Presentation lives in status-indicator.css: .voice-status-overlay,
            .voice-dot and its state variants, .voice-btn-small. The inline
            styles this replaced were not merely redundant. The dot's inline
            `background: var(--accent)` outranked .voice-dot.processing and
            .voice-dot.speaking, so the state getDotClass() computes had no
            visible effect. The <style> block this also removed defined a third
            `@keyframes fadeIn`; injected at runtime it was appended after the
            linked stylesheets, so it won the cascade and put a
            translateX(-50%) on every frame of the modal overlay's fadeIn. */}
        <div class="voice-status-overlay">
          <span class={getDotClass()} />
          <span>{getStatusText()}</span>
          <button
            class="voice-btn-small danger"
            onClick={() => props.onStopRecording()}
          >
            {props.isDictationMode ? 'Stop' : 'Cancel'}
          </button>
        </div>
      </Show>
    </Show>
  )
}