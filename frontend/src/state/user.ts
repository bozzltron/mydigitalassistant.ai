import { createSignal } from 'solid-js'
import { getUsers } from '../services/api'
import { setActingUser } from '../services/actingUser'

// User type definition
export interface User {
  id: number
  name: string
  email?: string
}

// Create signal for user state
export const [user, setUser] = createSignal<User | null>(null)
export const [isLoadingUser, setIsLoadingUser] = createSignal(false)

/**
 * Load the household account from the backend.
 *
 * There is no auth layer: the backend exposes `GET /users` and the app scopes
 * memory by user id. Prefer the id the user last used (localStorage), else the
 * first account. Previously this was a mock that always returned `{id: 1}`,
 * which silently assumed an account with id 1 existed.
 *
 * This is the one place the acting account is decided; `setActingUser` hands it to
 * the API layer so every request carries it instead of falling back to a literal.
 *
 * **Idempotent while a call is in flight.** Two callers race for it legitimately:
 * `index.tsx` resolves the account before the router renders (so no page mounts
 * without one), and `useAppInit` awaits it for its own ordering. Without the guard
 * that is two `GET /users` on the chat route.
 */
let inFlight: Promise<void> | null = null

export const fetchUser = (): Promise<void> => {
  inFlight ??= loadUser().finally(() => {
    inFlight = null
  })
  return inFlight
}

const loadUser = async () => {
  setIsLoadingUser(true)
  try {
    const users = await getUsers()
    if (users.length === 0) {
      console.warn('No accounts found on the backend; create one via POST /users')
      setActingUser(null)
      return
    }
    const savedId = typeof localStorage !== 'undefined'
      ? Number(localStorage.getItem('user_id'))
      : NaN
    const chosen = users.find((u) => u.id === savedId) ?? users[0]
    if (!chosen) return
    setUser({ id: chosen.id, name: chosen.name })
    setActingUser(chosen.id)
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('user_id', String(chosen.id))
    }
  } catch (error) {
    console.error('Failed to fetch user:', error)
  } finally {
    setIsLoadingUser(false)
  }
}
