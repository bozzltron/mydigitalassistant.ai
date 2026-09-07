import { createSignal } from 'solid-js'

// User type definition
export interface User {
  id: number
  name: string
  email?: string
}

// Create signal for user state
export const [user, setUser] = createSignal<User | null>(null)
export const [isLoadingUser, setIsLoadingUser] = createSignal(false)

// Mock function to simulate fetching user data
export const fetchUser = async () => {
  setIsLoadingUser(true)
  try {
    // This would normally be an API call
    await new Promise(resolve => setTimeout(resolve, 500))
    setUser({
      id: 1,
      name: 'Assistant User',
    })
  } catch (error) {
    console.error('Failed to fetch user:', error)
  } finally {
    setIsLoadingUser(false)
  }
}