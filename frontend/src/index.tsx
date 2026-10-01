/* @refresh reload */
import { ErrorBoundary } from 'solid-js'
import { render } from 'solid-js/web'
import { MetaProvider } from '@solidjs/meta'
import { Router } from '@solidjs/router'
import { routes } from './routes'
import ExternalLinkGuard from './components/ui/ExternalLinkGuard'
import './styles/index.css'

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
      <Router base="/">
        {routes}
      </Router>
    </ErrorBoundary>
  </MetaProvider>
), document.getElementById('root')!)