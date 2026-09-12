/* @refresh reload */
import { render } from 'solid-js/web'
import { MetaProvider } from '@solidjs/meta'
import { Router } from '@solidjs/router'
import { routes } from './routes'
import './styles/index.css'

render(() => (
  <MetaProvider>
    <Router base="/">
      {routes}
    </Router>
  </MetaProvider>
), document.getElementById('root')!)