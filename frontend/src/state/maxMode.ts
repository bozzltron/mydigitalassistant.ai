/**
 * Max intelligence is a **mode**, not a keystroke.
 *
 * The toggle used to live only in `ChatPage`'s component state, so a reload put the
 * user back on the standard model mid-project without saying so — which read as the
 * button not working. The user picks a model tier for the work they are doing; that
 * choice should outlive a page load.
 *
 * The backend remains the authority on whether a MAX model is configured: if
 * `MAX_MODEL` is empty the flag is harmless, and the turn falls back to the chat
 * model (`_select_generation`).
 */
const STORAGE_KEY = 'max_intelligence'

export function readMaxMode(): boolean {
  if (typeof localStorage === 'undefined') return false
  return localStorage.getItem(STORAGE_KEY) === 'on'
}

export function writeMaxMode(on: boolean): void {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(STORAGE_KEY, on ? 'on' : 'off')
}
