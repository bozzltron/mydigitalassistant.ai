import { createStore } from 'solid-js/store'

// Settings type definition matching original chat.html
export interface Settings {
  theme: 'light' | 'dark'
  ttsEnabled: boolean
  voiceUri: string
  voiceMode: boolean
  voiceSpeed: number
  voicePitch: number
  voiceVolume: number
  traceVisible: boolean
  braveEnabled: boolean
  language: string
  soundEffectsEnabled: boolean
}

// Create store for settings
const [settings, setSettings] = createStore<Settings>({
  theme: 'dark',
  ttsEnabled: true,
  voiceUri: '',
  voiceMode: false,
  voiceSpeed: 1.0,
  voicePitch: 1.0,
  voiceVolume: 1.0,
  traceVisible: false,
  braveEnabled: false,
  language: 'en',
  soundEffectsEnabled: false
})

// Save settings to localStorage
export const saveSettings = () => {
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem('assistant_settings', JSON.stringify(settings))
    } catch (error) {
      console.error('Failed to save settings:', error)
    }
  }
}

// Load settings from localStorage
export const loadSettings = () => {
  if (typeof localStorage !== 'undefined') {
    try {
      const saved = localStorage.getItem('assistant_settings')
      if (saved) {
        const parsed = JSON.parse(saved)
        setSettings(parsed)
      }
    } catch (error) {
      console.error('Failed to load settings:', error)
    }
  }
}

// Update a single setting
export const updateSetting = <K extends keyof Settings>(key: K, value: Settings[K]) => {
  setSettings(key, value)
  saveSettings()
}

// Export the settings and update functions
export { settings, setSettings }

// Load settings on initialization
loadSettings()