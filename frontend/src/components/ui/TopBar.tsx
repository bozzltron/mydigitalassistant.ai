import { createSignal, createEffect, For, onMount, Show, createMemo } from 'solid-js'
import { Session } from '../../state/session'
import { user } from '../../state/user'
import { settings, updateSetting } from '../../state/settings'
import { enterVoiceMode, exitVoiceMode, voice, isListening, isProcessing, isSpeaking, isIdle, isTtsSpeaking, stopRecording, setTtsSpeaking, isVoiceModeActive } from '../../state/voice'
import { api, getDeletedSessions, updateConversationTitle, deleteConversation } from '../../services/api'
import TrashCan from '../chat/TrashCan'
import { AlertsPanel } from './AlertsPanel'
import { EditModal } from './EditModal'
import { Modal } from './Modal'
import styles from './TopBar.module.css'

interface TopBarProps {
  conversations: Session[]
  activeConversation: Session | null
  onConversationChange: (conversationId: string) => void
  onNewConversationClick?: () => void
  isLoading: boolean
  assistantName: string
  onRefreshConversations?: () => Promise<void>
}

export default function TopBar(props: TopBarProps) {
  const [showSettings, setShowSettings] = createSignal(false)
  const [editSessionId, setEditSessionId] = createSignal<string | null>(null)
  const [editTitle, setEditTitle] = createSignal('')
  const [braveConfigured, setBraveConfigured] = createSignal(false)
  const [archiveConfirmSessionId, setArchiveConfirmSessionId] = createSignal<string | null>(null)

  // Use the same session_id key as useActiveConversation for consistency
  const [selectedSessionId, setSelectedSessionId] = createSignal<string | null>(() => {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem('session_id')
    }
    return null
  })

  // Update document title when assistant name changes
  createEffect(() => {
    document.title = props.assistantName
  })

  // Fetch conversations on mount
  onMount(async () => {
    const userId = user()?.id ?? 1
    if (!userId) return

    // Fetch trash sessions for TrashCan component
    const _trash = await getDeletedSessions(userId)
    // TrashCan handles its own state internally
  })

  // Load voices on mount
  onMount(() => {
    if (window.speechSynthesis) {
      speechSynthesis.onvoiceschanged = populateVoices
    }
    // Fetch backend settings (Brave toggle visibility)
    fetchBackendSettings()
    // Apply settings on mount
    applySettings()
  })

  // Ref for dropdown click-outside detection
  let dropdownRef: HTMLDivElement
  let voiceSelectRef: HTMLSelectElement

  // Close dropdown when clicking outside
  onMount(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef && !dropdownRef.contains(e.target as Node)) {
        setShowConversationDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  })

  const populateVoices = () => {
    const voices = speechSynthesis.getVoices()
    const localVoices = voices.filter(v => !v.url)
    const voiceSelect = voiceSelectRef
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

    // Apply Brave setting - only for search settings section visibility
    const searchSettingsSection = document.getElementById('search-settings-section')
    if (searchSettingsSection) {
      searchSettingsSection.style.display = s.braveEnabled ? 'block' : 'none'
    }
  }

  const handleTTSCChange = (e: Event) => {
    updateSetting('ttsEnabled', (e.target as HTMLInputElement).checked)
  }

  const handleSoundEffectsChange = (e: Event) => {
    updateSetting('soundEffectsEnabled', (e.target as HTMLInputElement).checked)
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

  const handleTraceVisibleChange = (e: Event) => {
    const checked = (e.target as HTMLInputElement).checked
    updateSetting('traceVisible', checked)
    const tracePanel = document.getElementById('trace-panel')
    if (tracePanel) tracePanel.classList.toggle('hidden', !checked)
  }

  const handleBraveChange = (e: Event) => {
    const checked = (e.target as HTMLInputElement).checked
    updateSetting('braveEnabled', checked)
    const searchSettingsSection = document.getElementById('search-settings-section')
    if (searchSettingsSection) {
      searchSettingsSection.style.display = checked ? 'block' : 'none'
    }
  }

  // Declarative voice status computed signals
  const voiceStatusText = createMemo(() => {
    const v = voice
    if (isListening()) return v.isDictating ? 'Dictating...' : 'Listening...'
    if (isProcessing()) return 'Transcribing...'
    if (isSpeaking() || isTtsSpeaking()) return 'Speaking...'
    return 'Idle'
  })

  const voiceStatusClass = createMemo(() => {
    if (isProcessing()) return 'processing'
    if (isSpeaking() || isTtsSpeaking()) return 'speaking'
    return ''
  })

  const voiceStatusActive = createMemo(() => !isIdle())

  const stopBtnText = createMemo(() => 
    isListening() || isProcessing() || isSpeaking() || isTtsSpeaking() 
      ? "I'm done talking" 
      : 'Start'
  )

  const showStopSpeakingBtn = createMemo(() => isTtsSpeaking())

  // Custom dropdown for conversation selection
  const [showConversationDropdown, setShowConversationDropdown] = createSignal(false)
  
  // Use selectedSessionId (from localStorage) to find title in conversations list,
  // fallback to activeConversation from props, then default
  const currentTitle = createMemo(() => 
    props.conversations.find(c => c.id === selectedSessionId())?.title
      ?? props.activeConversation?.title
      ?? '-- New Conversation --'
  )

  return (
    <>
      <header>
        <h1 id="agent-name">{props.assistantName}</h1>
        <div class={styles.conversationSwitcher}>
          <div class={styles.conversationDropdown} ref={(el) => { dropdownRef = el; }}>
            <button
              id="conversation-trigger"
              class={styles.conversationTrigger}
              onClick={() => setShowConversationDropdown(!showConversationDropdown())}
              disabled={props.isLoading}
              aria-haspopup="listbox"
              aria-expanded={showConversationDropdown()}
            >
              <span class={styles.conversationTriggerTitle}>
                {currentTitle()}
              </span>
              <svg class={showConversationDropdown() ? `${styles.conversationTriggerChevron} ${styles.isOpen}` : styles.conversationTriggerChevron} width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>

            {showConversationDropdown() && (
              <div class={styles.conversationDropdownMenu}>
                <div class={styles.conversationDropdownMenuContent}>
                  <For each={props.conversations}>
                    {(conv: Session) => (
                      <div
                        class={`${styles.conversationItem} ${conv.id === selectedSessionId() ? styles.isActive : ''}`}
                        onClick={() => {
                          props.onConversationChange(conv.id)
                          setSelectedSessionId(conv.id)
                          localStorage.setItem('session_id', conv.id)
                          setShowConversationDropdown(false)
                        }}
                      >
                        <span class={styles.conversationItemTitle}>
                          {conv.title || 'Conversation'}
                        </span>
                        <div class={styles.conversationItemActions}>
                          <button
                            class={styles.conversationItemAction}
                            title="Edit conversation name"
                            onClick={(e) => {
                              e.stopPropagation()
                              setEditSessionId(conv.id)
                              setEditTitle(conv.title || '')
                              setShowConversationDropdown(false)
                            }}
                          >
                            <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                            </svg>
                          </button>
                          <button
                            class={styles.conversationItemAction}
                            title="Archive conversation"
                            onClick={(e) => {
                              e.stopPropagation()
                              console.log('[Archive] Clicked for conversation:', conv.id, conv.title)
                              setArchiveConfirmSessionId(conv.id)
                              setShowConversationDropdown(false)
                            }}
                          >
                            <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                              <path d="M21 3l-7 7-3-3M3 3l18 18M21 3l-6.5 6.5M16 3l-3.5 3.5M3 21l7-7 3 3"/>
                            </svg>
                          </button>
                        </div>
                      </div>
                    )}
                  </For>
                </div>
              </div>
            )}
          </div>
          <button
            id="new-conversation-btn"
            class={styles.newConversationBtn}
            onClick={() => props.onNewConversationClick?.()}
          >New</button>
        </div>
        <div class="header-right">
          <AlertsPanel />
          <TrashCan />
          <span class="voice-status-bar" id="voice-status-bar" classList={{ active: voiceStatusActive() }}>
            <span class="voice-dot" id="voice-status-dot" classList={{ [voiceStatusClass()]: true }} />
            <span id="voice-status-text">{voiceStatusText()}</span>
            <button 
              class="voice-btn-small" 
              id="voice-stop-inline"
              onClick={() => stopRecording()}
              title="Stop recording and transcribe (I'm done talking)"
            >
              {stopBtnText()}
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
          <Show when={showStopSpeakingBtn()}>
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
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 1-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
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
            <input
              type="checkbox"
              id="tts-enabled"
              checked={settings.ttsEnabled}
              onChange={handleTTSCChange}
            />
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Play sound effects (listening/transcribing)</span>
            <input
              type="checkbox"
              id="sound-effects-enabled"
              checked={settings.soundEffectsEnabled}
              onChange={handleSoundEffectsChange}
            />
          </div>
          <div class="settings-section">
            <label for="voice-select">Voice</label>
            <select id="voice-select" ref={(el) => { voiceSelectRef = el; populateVoices(); }} onChange={handleVoiceSelectChange}>
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
            <input
              type="checkbox"
              id="trace-visible"
              checked={settings.traceVisible}
              onChange={handleTraceVisibleChange}
            />
          </div>
        </div>

        <div class="settings-section" id="search-settings-section" style={{"display": braveConfigured() && settings.braveEnabled ? 'block' : 'none'}}>
          <label>Search backend</label>
          <div class="toggle-row">
            <span class="toggle-label">Use Brave Search API</span>
            <input
              type="checkbox"
              id="brave-enabled"
              checked={settings.braveEnabled}
              onChange={handleBraveChange}
            />
          </div>
          <p style={{"font-size":"0.75rem","color":"var(--text-dim)","margin-top":"0.25rem"}}>
            When enabled, queries are sent to Brave's servers. Only available when a Brave API key is configured.
          </p>
        </div>
      </div>

      {/* Edit modal */}
      <EditModal
        isOpen={!!editSessionId()}
        onClose={() => {
          setEditSessionId(null)
          setEditTitle('')
        }}
        onSave={(newTitle) => {
          const sessionId = editSessionId()
          if (sessionId) {
            const userId = user()?.id ?? 1
            updateConversationTitle(sessionId, userId, newTitle).then(() => {
              setEditSessionId(null)
              setEditTitle('')
              if (props.onRefreshConversations) {
                props.onRefreshConversations()
              }
            }).catch((error) => {
              console.error('Failed to save title:', error)
            })
          }
        }}
        sessionId={editSessionId() ?? ''}
        currentTitle={editTitle()}
      />

      {/* Archive confirmation modal */}
      <Modal
        isOpen={!!archiveConfirmSessionId()}
        onClose={() => setArchiveConfirmSessionId(null)}
        title="Archive Conversation"
        size="small"
      >
        <div class="modal-content">
          <p>Are you sure you want to archive this conversation? It will be moved to the trash can and can be restored later.</p>
          <div class="modal-actions">
            <button
              class="btn-secondary"
              onClick={() => setArchiveConfirmSessionId(null)}
            >
              Cancel
            </button>
            <button
              class="btn-primary"
              onClick={async () => {
                const sessionId = archiveConfirmSessionId()
                if (!sessionId) return
                const u = user()
                if (u) {
                  console.log('[Archive] Calling deleteConversation with:', sessionId, u.id)
                  await deleteConversation(sessionId, u.id)
                  console.log('[Archive] Delete complete, refreshing conversations')
                  if (props.onRefreshConversations) {
                    await props.onRefreshConversations()
                  }
                  console.log('[Archive] Refresh complete')
                  // Notify trash can to refresh if open
                  window.dispatchEvent(new CustomEvent('conversation-archived'))
                }
                setArchiveConfirmSessionId(null)
              }}
            >
              Archive
            </button>
          </div>
        </div>
      </Modal>
    </>
  )}
