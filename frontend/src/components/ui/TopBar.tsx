import { createSignal, createEffect, For, onMount, onCleanup, Show, createMemo } from 'solid-js'
import type { Session } from '../../state/session'
import { user } from '../../state/user'
import { settings, updateSetting } from '../../state/settings'
import { enterVoiceMode, exitVoiceMode, voice, isListening, isProcessing, isIdle, isTtsSpeaking, stopRecording, cancelSpeech } from '../../state/voice'
import { getSettings, updateConversationTitle, deleteConversation } from '../../services/api'
import { debug } from '../../services/logger'
import TrashCan from '../chat/TrashCan'
import { AlertsPanel } from './AlertsPanel'
import { EditModal } from './EditModal'
import { Modal } from './Modal'
import styles from './TopBar.module.css'
import { PlusIcon, MicIcon, FolderIcon, BrainIcon, GearIcon } from './TopBarIcons'

interface TopBarProps {
  conversations: Session[]
  activeConversation: Session | null
  onConversationChange: (conversationId: string) => void
  onNewConversationClick?: () => void
  isLoading: boolean
  assistantName: string
  onRefreshConversations?: () => Promise<void>
}

// Names that make a pleasant default when the user has not chosen a voice yet.
const PREFERRED_VOICE_NAMES = [
  'Samantha',
  'Google US English',
  'Microsoft Aria',
  'Alex',
  'Karen',
  'Victoria',
  'Moira',
]

export default function TopBar(props: TopBarProps) {
  const [showSettings, setShowSettings] = createSignal(false)
  const [editSessionId, setEditSessionId] = createSignal<string | null>(null)
  const [editTitle, setEditTitle] = createSignal('')
  const [braveConfigured, setBraveConfigured] = createSignal(false)
  const [archiveConfirmSessionId, setArchiveConfirmSessionId] = createSignal<string | null>(null)

  // Local voices, rendered declaratively. Previously the <select> was built
  // imperatively with innerHTML/appendChild against a DOM ref.
  const [voices, setVoices] = createSignal<SpeechSynthesisVoice[]>([])

  // The voice <select> element, so its value can be re-applied once options exist.
  let voiceSelectEl: HTMLSelectElement | undefined

  // Draft values for the sliders so the label follows the thumb during a drag
  // without writing to localStorage on every input event; onChange persists.
  const [speedDraft, setSpeedDraft] = createSignal(settings.voiceSpeed)
  const [pitchDraft, setPitchDraft] = createSignal(settings.voicePitch)
  const [volumeDraft, setVolumeDraft] = createSignal(settings.voiceVolume)

  // Use the same session_id key as useActiveConversation for consistency
  const [selectedSessionId, setSelectedSessionId] = createSignal<string | null>(
    typeof localStorage !== 'undefined' ? localStorage.getItem('session_id') : null
  )

  // Update document title when assistant name changes
  createEffect(() => {
    document.title = props.assistantName
  })

  const loadVoices = () => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return
    // Local voices only; the platform's `localService` flag is the standard
    // signal for this (the old code read the non-standard `voice.url`).
    setVoices(speechSynthesis.getVoices().filter(v => v.localService))
  }

  onMount(() => {
    if ('speechSynthesis' in window) {
      speechSynthesis.onvoiceschanged = loadVoices
      loadVoices()
    }
    fetchBackendSettings()
  })

  onCleanup(() => {
    if ('speechSynthesis' in window) {
      speechSynthesis.onvoiceschanged = null
    }
  })

  // Pick a sensible default voice once voices are known and the user has none.
  createEffect(() => {
    const list = voices()
    if (settings.voiceUri || list.length === 0) return
    const preferred = list.find(v => PREFERRED_VOICE_NAMES.some(p => v.name.includes(p))) ?? list[0]
    if (preferred) updateSetting('voiceUri', preferred.voiceURI)
  })

  // Re-apply the stored voice after the options exist. Chrome returns an empty
  // list from getVoices() until `voiceschanged` fires, so on first paint the
  // <select> has no option matching settings.voiceUri and the browser drops the
  // value to "". The reactive `value` binding does not re-run when options are
  // added, so the panel showed the wrong voice (looking like the setting had not
  // persisted) until the user re-picked it.
  createEffect(() => {
    voices()
    if (voiceSelectEl) voiceSelectEl.value = settings.voiceUri
  })

  // Keep the draft sliders in sync when settings load or change externally.
  createEffect(() => {
    setSpeedDraft(settings.voiceSpeed)
    setPitchDraft(settings.voicePitch)
    setVolumeDraft(settings.voiceVolume)
  })

  // Ref for dropdown click-outside detection
  let dropdownRef: HTMLDivElement

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

  const fetchBackendSettings = async () => {
    try {
      const backendSettings = await getSettings()
      setBraveConfigured(backendSettings.brave_configured)
      if (backendSettings.brave_configured) {
        updateSetting('braveEnabled', backendSettings.brave_enabled)
      }
    } catch {
      // Ignore — the Brave section stays hidden when settings are unavailable.
    }
  }

  const handleTTSCChange = (e: Event) => {
    updateSetting('ttsEnabled', (e.target as HTMLInputElement).checked)
  }

  const handleSoundEffectsChange = (e: Event) => {
    updateSetting('soundEffectsEnabled', (e.target as HTMLInputElement).checked)
  }

  const handleVoiceSelectChange = (e: Event) => {
    updateSetting('voiceUri', (e.target as HTMLSelectElement).value)
  }

  const handleTraceVisibleChange = (e: Event) => {
    updateSetting('traceVisible', (e.target as HTMLInputElement).checked)
  }

  const handleBraveChange = (e: Event) => {
    updateSetting('braveEnabled', (e.target as HTMLInputElement).checked)
  }

  // Declarative voice status computed signals
  const voiceStatusText = createMemo(() => {
    const v = voice
    // Precedence matters: the agent speaking shuts the mic, so it must not read
    // as "Listening..."; and "Transcribing..." overrides "Listening..." while a
    // /transcribe request is outstanding (the mic is still open -- see the
    // conversation hook). One string, most-informative first.
    if (isTtsSpeaking()) return 'Speaking...'
    if (isProcessing()) return 'Transcribing...'
    if (isListening()) return v.isDictating ? 'Dictating...' : 'Listening...'
    return 'Idle'
  })

  const voiceStatusClass = createMemo(() => {
    if (isTtsSpeaking()) return 'speaking'
    if (isProcessing()) return 'processing'
    return ''
  })

  const voiceStatusActive = createMemo(() => !isIdle())

  const stopBtnText = createMemo(() =>
    isListening() || isProcessing() || isTtsSpeaking()
      ? "I'm done talking"
      : 'Start'
  )

  const showStopSpeakingBtn = createMemo(() => isTtsSpeaking())

  // Custom dropdown for conversation selection
  const [showConversationDropdown, setShowConversationDropdown] = createSignal(false)

  // Keep the trigger in sync with the conversation the app has active (e.g. a
  // conversation created from the New dialog), not only the last user click.
  createEffect(() => {
    const id = props.activeConversation?.id
    if (id) setSelectedSessionId(id)
  })

  const toggleConversationDropdown = () => {
    const next = !showConversationDropdown()
    setShowConversationDropdown(next)
    // Refresh on open so the list order reflects the latest activity.
    if (next) void props.onRefreshConversations?.()
  }

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
              onClick={() => toggleConversationDropdown()}
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
                          <a
                            class={styles.conversationItemAction}
                            title="Download as text"
                            href={`/chat/session/${encodeURIComponent(conv.id)}/download?user_id=${user()?.id ?? 1}`}
                            download=""
                            onClick={(e) => e.stopPropagation()}
                          >
                            <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                              <polyline points="7 10 12 15 17 10"/>
                              <line x1="12" x2="12" y1="15" y2="3"/>
                            </svg>
                          </a>
                          <button
                            class={styles.conversationItemAction}
                            title="Archive conversation"
                            onClick={(e) => {
                              e.stopPropagation()
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
            class="topbar-btn"
            onClick={() => props.onNewConversationClick?.()}
          >
            <PlusIcon />
            New
          </button>
        </div>
        <div class="header-right">
          <AlertsPanel />
          {/* While a voice turn is live the status bar takes the button's place:
              it carries the same stop/cancel actions, so showing both is
              redundant and costs horizontal space. */}
          <Show
            when={voice.status === 'idle'}
            fallback={
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
            }
          >
            <button
              id="voice-mode-btn"
              class="topbar-btn"
              title="Voice conversation mode"
              onClick={() => {
                enterVoiceMode()
                updateSetting('voiceMode', true)
              }}
            >
              <MicIcon />
              Let's talk
            </button>
          </Show>
          <a href="/files" class="topbar-btn" title="File Browser">
            <FolderIcon />
            Files
          </a>
          <a href="/brain" class="topbar-btn" title="Brain Observatory">
            <BrainIcon />
            Brain
          </a>
          <button
            class="topbar-btn topbar-btn--icon"
            id="settings-toggle"
            title="Settings"
            aria-label="Settings"
            onClick={() => setShowSettings(true)}
          >
            <GearIcon />
          </button>
          <TrashCan />
          {/* Transient voice/TTS controls, kept out of the six-button order. */}
          <Show when={showStopSpeakingBtn()}>
            <button
              id="stop-speaking-btn"
              class="topbar-btn"
              title="Stop speaking"
              onClick={() => {
                // cancelSpeech, not a bare speechSynthesis.cancel(): the gate has
                // to stay shut for the reverb tail, and a raw cancel left it open
                // on the agent's last syllable -- which is how the mic picked the
                // tail up.
                cancelSpeech()
              }}
            >
              Stop
            </button>
          </Show>
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
            <select
              id="voice-select"
              ref={(el) => { voiceSelectEl = el; }}
              value={settings.voiceUri}
              onChange={handleVoiceSelectChange}
            >
              <Show when={voices().length === 0}>
                <option value="">Loading voices...</option>
              </Show>
              <For each={voices()}>
                {(v) => <option value={v.voiceURI}>{v.name} ({v.lang})</option>}
              </For>
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
              value={speedDraft()}
              onInput={(e) => setSpeedDraft(parseFloat(e.target.value))}
              onChange={(e) => updateSetting('voiceSpeed', parseFloat(e.target.value))}
            />
            <span id="voice-speed-value">{speedDraft().toFixed(1)}x</span>
          </div>
          <div class="settings-section">
            <label for="voice-pitch">Pitch</label>
            <input
              type="range"
              id="voice-pitch"
              min="0"
              max="2"
              step="0.1"
              value={pitchDraft()}
              onInput={(e) => setPitchDraft(parseFloat(e.target.value))}
              onChange={(e) => updateSetting('voicePitch', parseFloat(e.target.value))}
            />
            <span id="voice-pitch-value">{pitchDraft().toFixed(1)}x</span>
          </div>
          <div class="settings-section">
            <label for="voice-volume">Volume</label>
            <input
              type="range"
              id="voice-volume"
              min="0"
              max="1"
              step="0.1"
              value={volumeDraft()}
              onInput={(e) => setVolumeDraft(parseFloat(e.target.value))}
              onChange={(e) => updateSetting('voiceVolume', parseFloat(e.target.value))}
            />
            <span id="voice-volume-value">{volumeDraft()}</span>
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

        <Show when={braveConfigured() && settings.braveEnabled}>
          <div class="settings-section" id="search-settings-section">
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
            <p class="settings-hint">
              When enabled, queries are sent to Brave's servers. Only available when a Brave API key is configured.
            </p>
          </div>
        </Show>
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
            // Called from a promise continuation, not from a render, so there is
            // no tracked scope to read the prop in and none is needed: this is an
            // imperative refresh after a write.
            // eslint-disable-next-line solid/reactivity
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
                  debug('[Archive] Deleting conversation', sessionId)
                  await deleteConversation(sessionId, u.id)
                  if (props.onRefreshConversations) {
                    await props.onRefreshConversations()
                  }
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