/* @refresh reload */
import { createSignal, ErrorBoundary, Show } from 'solid-js'
import { render } from 'solid-js/web'
import { MetaProvider } from '@solidjs/meta'
import { Router } from '@solidjs/router'
import { routes } from './routes'
import { fetchUser } from './state/user'
import ExternalLinkGuard from './components/ui/ExternalLinkGuard'
import './styles/index.css'

// Resolve the account **before any route renders**.
//
// Every page's data is user-scoped, and `actingUser()` throws rather than guessing a
// default — so a page that mounts first fails its first read. That is what a full
// refresh at /tasks did (the tasks were gone), at /files (an error toast and an empty
// list), and at /brain (it looked fine only because it fell back to a hardcoded id).
//
// `App`'s `useAppInit` awaited this too, but `App` is only the `/` route, so a refresh
// on any other route never ran it at all. One gate, at the entry, covers every route.
const [ready, setReady] = createSignal(false)
void fetchUser().finally(() => setReady(true))

render(() => (
  <MetaProvider>
    <ErrorBoundary
      fallback={(err, reset) => (
        <div class="app-error" role="alert">
          <h2>Something went wrong</h2>
          <p class="app-error-detail">{String(err)}</p>
          <button class="btn-primary" onClick={() => reset()}>Try again</button>
        </div>
      )}
    >
      <ExternalLinkGuard />
      <Show when={ready()} fallback={<div class="app-loading">Loading…</div>}>
        <Router base="/">
          {routes}
        </Router>
      </Show>
    </ErrorBoundary>
  </MetaProvider>
), document.getElementById('root')!)