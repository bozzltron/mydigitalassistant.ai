import { createRoot } from "solid-js";

import App from './App'
import './styles/global.css'
import './styles/chat.css'
import './styles/brain.css'

createRoot(document.getElementById('root')!).render(<App />)