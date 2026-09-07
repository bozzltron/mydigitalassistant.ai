import { createSelector } from 'solid-js'
import { settings, updateSetting } from '../../state/settings'

export default function SettingsPage() {
  const isDarkMode = createSelector(() => settings().theme === 'dark')
  
  const toggleTheme = () => {
    updateSetting('theme', settings().theme === 'light' ? 'dark' : 'light')
  }
  
  const handleTTSChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    updateSetting('ttsEnabled', target.checked)
  }
  
  const handleBraveChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    updateSetting('braveEnabled', target.checked)
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
              checked={settings().ttsEnabled}
              onChange={handleTTSChange}
            />
            Enable Text-to-Speech
          </label>
        </div>
        
        <div class="setting-row">
          <label>Voice Selection:</label>
          <select value={settings().voiceSelect} onChange={(e) => updateSetting('voiceSelect', e.target.value)}>
            <option value="default">Default Voice</option>
            <option value="male">Male Voice</option>
            <option value="female">Female Voice</option>
          </select>
        </div>
        
        <div class="setting-row">
          <label>Speech Speed: {settings().speechSpeed.toFixed(1)}x</label>
          <input 
            type="range" 
            min="0.5" 
            max="2.0" 
            step="0.1" 
            value={settings().speechSpeed}
            onChange={(e) => updateSetting('speechSpeed', parseFloat(e.target.value))}
          />
        </div>
      </div>
      
      <div class="settings-section">
        <h3>Search</h3>
        
        <div class="setting-row">
          <label>
            <input 
              type="checkbox" 
              checked={settings().braveEnabled}
              onChange={handleBraveChange}
            />
            Enable Brave Search (requires API key)
          </label>
        </div>
      </div>
    </div>
  )
}