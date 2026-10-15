import { Route } from '@solidjs/router'
import App from './App'
import BrainPage from './components/brain/BrainPage'
import FilesPage from './components/files/FilesPage'
import MapPage from './components/maps/MapPage'
import TasksPage from './components/tasks/TasksPage'

export const routes = (
  <>
    <Route path="/" component={App} />
    <Route path="/brain" component={BrainPage} />
    <Route path="/files" component={FilesPage} />
    <Route path="/maps" component={MapPage} />
    <Route path="/tasks" component={TasksPage} />
  </>
)