

export default function SettingsPanel() {

  return (
    <>
      <div id="settings-panel">
        <div class="settings-title">
          Settings
          <button class="settings-close" id="settings-close">&times;</button>
        </div>

        <div class="settings-section">
          <label>Voice settings</label>
          <div class="toggle-row">
            <span class="toggle-label">Read responses aloud</span>
            <input type="checkbox" id="tts-enabled" />
          </div>
          <div class="settings-section">
            <label for="voice-select">Voice</label>
            <select id="voice-select">
              <option value="">Loading voices...</option>
            </select>
          </div>
          <div class="settings-section">
            <label for="voice-speed">Speed</label>
            <input type="range" id="voice-speed" min="0.5" max="2" step="0.1" value="1" />
            <span id="voice-speed-value">1.0x</span>
          </div>
          <div class="settings-section">
            <label for="voice-pitch">Pitch</label>
            <input type="range" id="voice-pitch" min="0" max="2" step="0.1" value="1" />
            <span id="voice-pitch-value">1.0x</span>
          </div>
          <div class="settings-section">
            <label for="voice-volume">Volume</label>
            <input type="range" id="voice-volume" min="0" max="1" step="0.1" value="1" />
            <span id="voice-volume-value">1.0</span>
          </div>
        </div>

        <div class="settings-section">
          <label>Interface</label>
          <div class="toggle-row">
            <span class="toggle-label">Show trace panel</span>
            <input type="checkbox" id="trace-visible" checked />
          </div>
        </div>

        <div class="settings-section" id="search-settings-section" style={{"display":"none"}}>
          <label>Search backend</label>
          <div class="toggle-row">
            <span class="toggle-label">Use Brave Search API</span>
            <input type="checkbox" id="brave-enabled" />
          </div>
          <p style={{"font-size":"0.75rem","color":"var(--text-dim)","margin-top":"0.25rem"}}>
            When enabled, queries are sent to Brave's servers. Only available when a Brave API key is configured.
          </p>
        </div>
      </div>
    </>
  )
}

