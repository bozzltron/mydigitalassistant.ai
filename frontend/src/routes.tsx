import { Route } from 'solid-router'

export const routes = [
  new Route('/', () => <Home />),
  new Route('/chat', () => import('./components/chat/ChatPage')),
  new Route('/brain', () => import('./components/brain/BrainPage')),
  new Route('/files', () => import('./components/files/FilesPage')),
  new Route('/settings', () => import('./components/settings/SettingsPage')),
]