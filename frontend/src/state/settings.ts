import { createStore } from 'solid-js/store'

// Settings type definition
export interface Settings {
  theme: 'light' | 'dark'
  ttsEnabled: boolean
  voiceSelect: string
  speechSpeed: number
  speechPitch: number
  braveEnabled: boolean
  language: string
}

// Create store for settings
const [settings, setSettings] = createStore<Settings>({
  theme: 'light',
  ttsEnabled: true,
  voiceSelect: 'default',
  speechSpeed: 1.0,
  speechPitch: 1.0,
  braveEnabled: false,
  language: 'en'
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