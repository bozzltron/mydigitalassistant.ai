import { createSelector } from 'solid-js'
import { settings, updateSetting } from '../../state/settings'

export default function SettingsPage() {
  const isDarkMode = createSelector(() => settings.theme === 'dark')
  
  const toggleTheme = () => {
    updateSetting('theme', settings.theme === 'light' ? 'dark' : 'light')
  }
  
  const handleTTSChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    updateSetting('ttsEnabled', target.checked)
  }
  
  const handleBraveChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    updateSetting('braveEnabled', target.checked)
  }
  
  const handleVoiceChange = (e: Event) => {
    const target = e.target as HTMLSelectElement
    updateSetting('voiceUri', target.value)
  }
  
  const handleSpeedChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    updateSetting('voiceSpeed', parseFloat(target.value))
  }
  
  const handlePitchChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    updateSetting('voicePitch', parseFloat(target.value))
  }
  
  const handleVolumeChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    updateSetting('voiceVolume', parseFloat(target.value))
  }

  return (
    <div class="settings-page">
      <h2>Settings</h2>
      
      <div class="settings-section">
        <h3>Appearance</h3>
        
        <div class="setting-row">
          <label>
            <input 
              type="checkbox" 
              checked={isDarkMode()}
              onChange={toggleTheme}
            />
            Dark Mode
          </label>
        </div>
      </div>
      
      <div class="settings-section">
        <h3>Speech</h3>
        
        <div class="setting-row">
          <label>
            <input 
              type="checkbox" 
              checked={settings.ttsEnabled}
              onChange={handleTTSChange}
            />
            Enable Text-to-Speech
          </label>
        </div>
        
        <div class="setting-row">
          <label>Voice Selection:</label>
          <select value={settings.voiceUri} onChange={handleVoiceChange}>
            <option value="">Default Voice</option>
            <option value="male">Male Voice</option>
            <option value="female">Female Voice</option>
          </select>
        </div>
        
        <div class="setting-row">
          <label>Speech Speed: {settings.voiceSpeed.toFixed(1)}x</label>
          <input 
            type="range" 
            min="0.5" 
            max="2.0" 
            step="0.1" 
            value={settings.voiceSpeed}
            onChange={handleSpeedChange}
          />
        </div>
        
        <div class="setting-row">
          <label>Speech Pitch: {settings.voicePitch.toFixed(1)}x</label>
          <input 
            type="range" 
            min="0.5" 
            max="2.0" 
            step="0.1" 
            value={settings.voicePitch}
            onChange={handlePitchChange}
          />
        </div>
        
        <div class="setting-row">
          <label>Speech Volume: {settings.voiceVolume.toFixed(1)}</label>
          <input 
            type="range" 
            min="0" 
            max="1" 
            step="0.1" 
            value={settings.voiceVolume}
            onChange={handleVolumeChange}
          />
        </div>
      </div>
      
      <div class="settings-section">
        <h3>Search</h3>
        
        <div class="setting-row">
          <label>
            <input 
              type="checkbox" 
              checked={settings.braveEnabled}
              onChange={handleBraveChange}
            />
            Enable Brave Search (requires API key)
          </label>
        </div>
      </div>
    </div>
  )
}