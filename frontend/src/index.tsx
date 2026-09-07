/* @refresh reload */
import { render } from 'solid-js/web'
import { MetaProvider } from '@solidjs/meta'
import './index.css'
import App from './App.tsx'

render(() => (
  <MetaProvider>
    <App />
  </MetaProvider>
), document.getElementById('root')!)