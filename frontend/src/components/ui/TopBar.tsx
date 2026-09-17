import { createEffect, createSignal, onMount, Show } from 'solid-js'
import { Session } from '../../state/session'
import { user } from '../../state/user'
import { settings, updateSetting } from '../../state/settings'
import { enterVoiceMode, exitVoiceMode, voice, isListening, isProcessing, isSpeaking, isIdle, isTtsSpeaking, stopRecording, setTtsSpeaking } from '../../state/voice'
import { api } from '../../services/api'

interface TopBarProps {
  conversations: Session[]
  activeConversation: Session | null
  onConversationChange: (conversationId: string) => void
  onNewConversationClick?: () => void
  isLoading: boolean
  assistantName: string
}

export default function TopBar(props: TopBarProps) {
  const [showSettings, setShowSettings] = createSignal(false)
  const [braveConfigured, setBraveConfigured] = createSignal(false)

  const u = user()

  // Update document title when assistant name changes
  createEffect(() => {
    document.title = props.assistantName
  })

  // Populate conversation dropdown
  createEffect(() => {
    const selectElement = document.getElementById('conversation-select') as HTMLSelectElement | null
    if (selectElement && props.conversations) {
      while (selectElement.options.length > 1) {
        selectElement.remove(1)
      }

      props.conversations.forEach(conversation => {
        const option = document.createElement('option')
        option.value = conversation.id
        option.text = conversation.title || 'Conversation'
        selectElement.add(option)
      })

      if (props.activeConversation) {
        selectElement.value = props.activeConversation.id
      } else {
        selectElement.value = ''
      }
    }
  })

  const handleConversationSelect = (e: Event) => {
    const selectElement = e.target as HTMLSelectElement
    const selectedId = selectElement.value
    if (selectedId) {
      props.onConversationChange(selectedId)
    }
  }

  // Load voices on mount
  onMount(() => {
    if (window.speechSynthesis) {
      speechSynthesis.onvoiceschanged = populateVoices
      populateVoices()
    }
    // Fetch backend settings (Brave toggle visibility)
    fetchBackendSettings()
    // Apply settings on mount
    applySettings()
  })

  const populateVoices = () => {
    const voices = speechSynthesis.getVoices()
    const localVoices = voices.filter(v => !v.url)
    const voiceSelect = document.getElementById('voice-select') as HTMLSelectElement
    if (!voiceSelect) return

    const currentSettings = settings
    const preferredNames = [
      'Samantha',
      'Google US English',
      'Microsoft Aria',
      'Alex',
      'Karen',
      'Victoria',
      'Moira',
    ]

    let defaultIndex = -1
    if (!currentSettings.voiceUri) {
      for (let i = 0; i < localVoices.length; i++) {
        const name = localVoices[i].name
        if (preferredNames.some(p => name.includes(p))) {
          defaultIndex = i
          break
        }
      }
      if (defaultIndex === -1) defaultIndex = 0
    }

    voiceSelect.innerHTML = ''
    if (localVoices.length === 0) {
      voiceSelect.innerHTML = '<option value="">No local voices found</option>'
      return
    }

    localVoices.forEach((v, i) => {
      const opt = document.createElement('option')
      opt.value = v.voiceURI
      opt.textContent = `${v.name} (${v.lang})`
      if (currentSettings.voiceUri && v.voiceURI === currentSettings.voiceUri) opt.selected = true
      if (!currentSettings.voiceUri && i === defaultIndex) opt.selected = true
      voiceSelect.appendChild(opt)
    })
    if (!currentSettings.voiceUri && defaultIndex >= 0) {
      updateSetting('voiceUri', localVoices[defaultIndex].voiceURI)
    }
  }

  const fetchBackendSettings = async () => {
    try {
      const backendSettings = await api('/settings')
      setBraveConfigured(backendSettings.brave_configured)
      if (backendSettings.brave_configured) {
        updateSetting('braveEnabled', backendSettings.brave_enabled)
      }
    } catch {
      // Ignore
    }
  }

  const applySettings = () => {
    const s = settings

    // Apply voice settings
    const voiceSpeed = document.getElementById('voice-speed') as HTMLInputElement
    const voicePitch = document.getElementById('voice-pitch') as HTMLInputElement
    const voiceVolume = document.getElementById('voice-volume') as HTMLInputElement
    const speedValue = document.getElementById('voice-speed-value')
    const pitchValue = document.getElementById('voice-pitch-value')
    const volumeValue = document.getElementById('voice-volume-value')

    if (voiceSpeed) voiceSpeed.value = s.voiceSpeed
    if (voicePitch) voicePitch.value = s.voicePitch
    if (voiceVolume) voiceVolume.value = s.voiceVolume

    if (speedValue) speedValue.textContent = `${s.voiceSpeed}x`
    if (pitchValue) pitchValue.textContent = `${s.voicePitch}x`
    if (volumeValue) volumeValue.textContent = s.voiceVolume.toString()

    // Apply TTS setting
    const ttsEnabled = document.getElementById('tts-enabled') as HTMLInputElement
    if (ttsEnabled) ttsEnabled.checked = s.ttsEnabled

    // Apply Brave setting
    const braveEnabled = document.getElementById('brave-enabled') as HTMLInputElement
    const searchSettingsSection = document.getElementById('search-settings-section')
    if (braveEnabled) {
      braveEnabled.checked = s.braveEnabled
    }
    if (searchSettingsSection) {
      searchSettingsSection.style.display = s.braveEnabled ? 'block' : 'none'
    }
  }

  const handleTTSCChange = () => {
    const ttsEnabled = document.getElementById('tts-enabled') as HTMLInputElement
    if (ttsEnabled) updateSetting('ttsEnabled', ttsEnabled.checked)
  }

  const handleVoiceSelectChange = (e: Event) => {
    const select = e.target as HTMLSelectElement
    updateSetting('voiceUri', select.value)
  }

  const handleVoiceSpeedChange = (e: Event) => {
    const input = e.target as HTMLInputElement
    const speedValue = document.getElementById('voice-speed-value')
    if (speedValue) speedValue.textContent = `${input.value}x`
  }

  const handleVoiceSpeedSave = (e: Event) => {
    const input = e.target as HTMLInputElement
    updateSetting('voiceSpeed', parseFloat(input.value))
  }

  const handleVoicePitchChange = (e: Event) => {
    const input = e.target as HTMLInputElement
    const pitchValue = document.getElementById('voice-pitch-value')
    if (pitchValue) pitchValue.textContent = `${input.value}x`
  }

  const handleVoicePitchSave = (e: Event) => {
    const input = e.target as HTMLInputElement
    updateSetting('voicePitch', parseFloat(input.value))
  }

  const handleVoiceVolumeChange = (e: Event) => {
    const input = e.target as HTMLInputElement
    const volumeValue = document.getElementById('voice-volume-value')
    if (volumeValue) volumeValue.textContent = input.value
  }

  const handleVoiceVolumeSave = (e: Event) => {
    const input = e.target as HTMLInputElement
    updateSetting('voiceVolume', parseFloat(input.value))
  }

  const handleTraceVisibleChange = () => {
    const traceVisible = document.getElementById('trace-visible') as HTMLInputElement
    const tracePanel = document.getElementById('trace-panel')
    if (traceVisible) {
      updateSetting('traceVisible', traceVisible.checked)
      if (tracePanel) tracePanel.classList.toggle('hidden', !traceVisible.checked)
    }
  }

  const handleBraveChange = () => {
    const braveEnabled = document.getElementById('brave-enabled') as HTMLInputElement
    const searchSettingsSection = document.getElementById('search-settings-section')
    if (braveEnabled) {
      updateSetting('braveEnabled', braveEnabled.checked)
    }
    if (searchSettingsSection) {
      searchSettingsSection.style.display = braveEnabled?.checked ? 'block' : 'none'
    }
  }

  // Update voice status bar based on voice state
  createEffect(() => {
    const statusBar = document.getElementById('voice-status-bar') as HTMLElement
    const statusDot = document.getElementById('voice-status-dot') as HTMLElement
    const statusText = document.getElementById('voice-status-text') as HTMLElement
    const stopBtn = document.getElementById('voice-stop-inline') as HTMLButtonElement
    const cancelBtn = document.getElementById('voice-cancel-inline') as HTMLButtonElement
    
    if (!statusBar || !statusDot || !statusText) return

    const v = voice
    const isActive = !isIdle()
    
    statusBar.classList.toggle('active', isActive)
    statusDot.classList.remove('processing', 'speaking')
    
    if (isListening()) {
      statusDot.classList.remove('processing', 'speaking')
      statusText.textContent = v.isDictating ? 'Dictating...' : 'Listening...'
      stopBtn.textContent = "I'm done talking"
      cancelBtn.textContent = 'Cancel'
    } else if (isProcessing()) {
      statusDot.classList.add('processing')
      statusText.textContent = 'Transcribing...'
      stopBtn.textContent = "I'm done talking"
      cancelBtn.textContent = 'Cancel'
    } else if (isSpeaking() || isTtsSpeaking()) {
      statusDot.classList.add('speaking')
      statusText.textContent = 'Speaking...'
      stopBtn.textContent = "I'm done talking"
      cancelBtn.textContent = 'Cancel'
    } else {
      statusText.textContent = 'Idle'
    }
  })

  return (
    <>
      <header>
        <h1 id="agent-name">{props.assistantName}</h1>
        <div class="conversation-switcher" style={{"white-space":"nowrap"}}>
          <select
            id="conversation-select"
            data-testid="conversation-select"
            class="select"
            style={{"font-size":"0.8rem","padding":"0.2rem 0.4rem"}}
            onChange={handleConversationSelect}
            disabled={props.isLoading}
          >
            <option value="">-- New Conversation --</option>
          </select>
          <button
            id="new-conversation-btn"
            class="voice-btn-small"
            style={{"padding":"0.2rem 0.4rem","font-size":"0.75rem","margin-left":"0.3rem"}}
            onClick={() => props.onNewConversationClick?.()}
          >New</button>
        </div>
        <div class="header-right">
          {u && <span class="user-badge" id="user-badge">{u.name}</span>}
          <span class="voice-status-bar" id="voice-status-bar">
            <span class="voice-dot" id="voice-status-dot" />
            <span id="voice-status-text">Listening</span>
            <button 
              class="voice-btn-small" 
              id="voice-stop-inline"
              onClick={() => stopRecording()}
              title="Stop recording and transcribe (I'm done talking)"
            >
              I'm done talking
            </button>
            <button 
              class="voice-btn-small danger" 
              id="voice-cancel-inline"
              onClick={() => {
                exitVoiceMode()
                updateSetting('voiceMode', false)
              }}
              title="Exit voice mode (cancel)"
            >
              Cancel
            </button>
          </span>
          <button 
            id="voice-mode-btn" 
            class="voice-btn" 
            title="Voice conversation mode" 
            onClick={() => {
              if (voice.status !== 'idle') {
                exitVoiceMode()
                updateSetting('voiceMode', false)
              } else {
                enterVoiceMode()
                updateSetting('voiceMode', true)
              }
            }}
            classList={{ active: voice.status !== 'idle' }}
          >
            {voice.status !== 'idle' ? 'Stop Conversation' : "Let's talk"}
          </button>
          <Show when={isTtsSpeaking()}>
            <button 
              id="stop-speaking-btn" 
              class="voice-btn" 
              title="Stop speaking" 
              onClick={() => {
                if ('speechSynthesis' in window) {
                  speechSynthesis.cancel()
                }
                setTtsSpeaking(false)
              }}
            >
              Stop
            </button>
          </Show>
          <button class="settings-btn" id="settings-toggle" title="Settings" onClick={() => setShowSettings(true)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </button>
          <a href="/brain" class="nav-link" title="Brain Observatory" style={{"color":"var(--text-dim)","text-decoration":"none","font-size":"0.8rem","padding":"0.35rem 0.75rem","border":"1px solid var(--border)","border-radius":"6px"}}>Brain</a>
          <a href="/files" class="nav-link" title="File Browser" style={{"color":"var(--text-dim)","text-decoration":"none","font-size":"0.8rem","padding":"0.35rem 0.75rem","border":"1px solid var(--border)","border-radius":"6px","margin-left":"0.5rem"}}>Files</a>
        </div>
      </header>

      <div
        id="settings-panel"
        class={`settings-panel ${showSettings() ? 'open' : ''}`}
      >
        <div class="settings-title">
          Settings
          <button class="settings-close" onClick={() => setShowSettings(false)}>&times;</button>
        </div>

        <div class="settings-section">
          <label>Voice settings</label>
          <div class="toggle-row">
            <span class="toggle-label">Read responses aloud</span>
            <input type="checkbox" id="tts-enabled" onChange={handleTTSCChange} />
          </div>
          <div class="settings-section">
            <label for="voice-select">Voice</label>
            <select id="voice-select" onChange={handleVoiceSelectChange}>
              <option value="">Loading voices...</option>
            </select>
          </div>
          <div class="settings-section">
            <label for="voice-speed">Speed</label>
            <input
              type="range"
              id="voice-speed"
              min="0.5"
              max="2"
              step="0.1"
              value={settings.voiceSpeed}
              onInput={handleVoiceSpeedChange}
              onChange={handleVoiceSpeedSave}
            />
            <span id="voice-speed-value">{settings.voiceSpeed.toFixed(1)}x</span>
          </div>
          <div class="settings-section">
            <label for="voice-pitch">Pitch</label>
            <input
              type="range"
              id="voice-pitch"
              min="0"
              max="2"
              step="0.1"
              value={settings.voicePitch}
              onInput={handleVoicePitchChange}
              onChange={handleVoicePitchSave}
            />
            <span id="voice-pitch-value">{settings.voicePitch.toFixed(1)}x</span>
          </div>
          <div class="settings-section">
            <label for="voice-volume">Volume</label>
            <input
              type="range"
              id="voice-volume"
              min="0"
              max="1"
              step="0.1"
              value={settings.voiceVolume}
              onInput={handleVoiceVolumeChange}
              onChange={handleVoiceVolumeSave}
            />
            <span id="voice-volume-value">{settings.voiceVolume}</span>
          </div>
        </div>

        <div class="settings-section">
          <label>Interface</label>
          <div class="toggle-row">
            <span class="toggle-label">Show trace panel</span>
            <input type="checkbox" id="trace-visible" onChange={handleTraceVisibleChange} />
          </div>
        </div>

        <div class="settings-section" id="search-settings-section" style={{"display": braveConfigured() && settings.braveEnabled ? 'block' : 'none'}}>
          <label>Search backend</label>
          <div class="toggle-row">
            <span class="toggle-label">Use Brave Search API</span>
            <input type="checkbox" id="brave-enabled" onChange={handleBraveChange} />
          </div>
          <p style={{"font-size":"0.75rem","color":"var(--text-dim)","margin-top":"0.25rem"}}>
            When enabled, queries are sent to Brave's servers. Only available when a Brave API key is configured.
          </p>
        </div>
      </div>
    </>
  )
}