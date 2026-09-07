import { Meta } from '@solidjs/meta'
import { createEffect, lazy, Suspense } from 'solid-js'
import { fetchUser } from './state/user'
import { loadSettings } from './state/settings'
import Home from './components/Home'
import ChatPage from './components/chat/ChatPage'

export default function App() {
  // Initialize on app start
  createEffect(() => {
    fetchUser()
    loadSettings()
  })

  return (
    <>
      <Meta title="Cognitive Assistant" />
      {/* Default to showing chat page in development */}
      <div style="display: flex; flex-direction: column; min-height: 100vh;">
        <Home />
        <ChatPage />
      </div>
    </>
  )
}