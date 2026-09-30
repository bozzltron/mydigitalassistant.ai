import { createSignal, onMount } from 'solid-js'
import { fetchUser } from '../state/user'
import { loadSettings } from '../state/settings'
import { getAssistantName } from '../services/api'
import { initChat } from '../state/chat'

export function useAppInit(assistantNameSignal: [() => string, (v: string) => void]) {
  const [initialized, setInitialized] = createSignal(false)

  const initializeApp = async () => {
    await fetchUser()
    loadSettings()

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

  // Runs once on mount. This was a createEffect with no dependencies, which is
  // mount-only work expressed the wrong way; the sibling `initializeOnce`
  // effect was a no-op (it wrote a localStorage flag nothing ever read).
  onMount(() => {
    void initializeApp()
  })

  return {
    initialized,
    setInitialized,
  }
}
