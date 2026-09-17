import { Route } from '@solidjs/router'
import App from './App'
import BrainPage from './components/brain/BrainPage'
import FilesPage from './components/files/FilesPage'

export const routes = (
  <>
    <Route path="/" component={App} />
    <Route path="/brain" component={BrainPage} />
    <Route path="/files" component={FilesPage} />
  </>
)