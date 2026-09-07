import { createEffect } from 'solid-js'
import { voice, enterVoiceMode, exitVoiceMode, startProcessing, startSpeaking } from '../../state/voice'

export default function VoiceControls() {
  const handleVoiceClick = () => {
    if (voice().isListening) {
      exitVoiceMode()
    } else {
      enterVoiceMode()
      
      // Simulate voice processing
      setTimeout(() => {
        startProcessing()
        
        setTimeout(() => {
          startSpeaking()
          
          setTimeout(() => {
            exitVoiceMode()
          }, 1000)
        }, 1000)
      }, 500)
    }
  }

  return (
    <div class="voice-controls">
      <button 
        class={`voice-button ${voice().isListening ? 'listening' : ''}`}
        onClick={handleVoiceClick}
        aria-label={voice().isListening ? "Stop listening" : "Start voice input"}
      >
        {voice().isListening ? '🛑' : '🎤'}
      </button>
      
      <Show when={voice().transcript}>
        <div class="transcript-display">
          {voice().transcript}
        </div>
      </Show>
    </div>
  )
}