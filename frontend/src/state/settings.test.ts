import { describe, it, expect, beforeEach } from 'vitest'
import { settings, updateSetting, loadSettings } from './settings'

describe('settings persistence', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('writes voice + speed to localStorage on update', () => {
    updateSetting('voiceUri', 'com.apple.voice.Samantha')
    updateSetting('voiceSpeed', 1.4)

    const saved = JSON.parse(localStorage.getItem('assistant_settings')!) as {
      voiceUri: string
      voiceSpeed: number
    }
    expect(saved.voiceUri).toBe('com.apple.voice.Samantha')
    expect(saved.voiceSpeed).toBe(1.4)
  })

  it('reloads voice + speed from localStorage', () => {
    localStorage.setItem(
      'assistant_settings',
      JSON.stringify({ voiceUri: 'urn:moz-tts:test', voiceSpeed: 1.7 }),
    )
    loadSettings()
    expect(settings.voiceUri).toBe('urn:moz-tts:test')
    expect(settings.voiceSpeed).toBe(1.7)
  })
})
