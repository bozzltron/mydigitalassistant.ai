import { createSignal, createEffect } from 'solid-js'
import { debug } from '../services/logger'
import { fetchUser } from '../state/user'
import { loadSettings } from '../state/settings'
import { getAssistantName } from '../services/api'
import { initChat } from '../state/chat'

export function useAppInit(assistantNameSignal: [() => string, (v: string) => void]) {
  const [initialized, setInitialized] = createSignal(false)

  const initializeApp = async () => {
    debug('APP INIT: Starting initialization...')
    await fetchUser()
    debug('APP INIT: fetchUser completed')
    loadSettings()
    debug('APP INIT: loadSettings completed')

    try {
      const result = await getAssistantName()
      if (result.name) {
        assistantNameSignal[1](result.name)
      }
    } catch (error) {
      console.error('Failed to fetch assistant name:', error)
    }

    initChat()
    setInitialized(true)
  }

  const initializeOnce = () => {
    const alreadyInitialized = localStorage.getItem('app_initialize_complete')

    if (alreadyInitialized) return
    localStorage.setItem('app_initialize_complete', 'true')

    const savedSessionId = localStorage.getItem('session_id')
    const u = { id: 1 } // placeholder - user() will be available after fetchUser

    if (savedSessionId && u) {
      debug('APP INIT: Restoring conversation, session_id =', savedSessionId)
      // Session messages will be loaded by useActiveConversation effect
    } else {
      if (!savedSessionId) debug('APP INIT: No saved session_id in localStorage')
      if (!u) debug('APP INIT: User not yet loaded')
    }
  }

  createEffect(() => {
    initializeApp()
  })

  createEffect(() => {
    initializeOnce()
  })

  return {
    initialized,
    setInitialized,
  }
}