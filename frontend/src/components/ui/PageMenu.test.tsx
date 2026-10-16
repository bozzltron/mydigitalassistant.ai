import { render, screen, fireEvent } from '@solidjs/testing-library'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PageMenu from './PageMenu'

vi.mock('../../services/api', () => ({
  // The menu reads settings to decide whether the Maps entry belongs in the list.
  getSettings: vi.fn(async () => ({ maps_enabled: false })),
}))

/** jsdom's URL is `/` by default, and the menu decides Chat's presence from it. */
function at(path: string) {
  window.history.pushState({}, '', path)
}

const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /pages/i }))
const items = () => screen.getAllByRole('menuitem').map((el) => el.textContent?.trim())

describe('PageMenu', () => {
  afterEach(() => at('/'))

  it('leads with Chat on every page but the chat', () => {
    at('/tasks')
    render(() => <PageMenu />)
    openMenu()

    // Chat is somewhere to *go* from here, so it leads the list.
    expect(items()[0]).toBe('Chat')
    expect(screen.getByRole('menuitem', { name: /chat/i })).toHaveAttribute('href', '/')
    expect(items()).toContain('Tasks')
  })

  it('omits Chat on the chat page — you are already there', () => {
    at('/')
    render(() => <PageMenu />)
    openMenu()

    expect(screen.queryByRole('menuitem', { name: /^chat$/i })).not.toBeInTheDocument()
    expect(items()[0]).toBe('Tasks')
  })

  it('leaves the chat-only controls out when not asked for', () => {
    // Trace and Settings dock panels that only exist on the chat page, so offering them
    // anywhere else would be an action that does nothing.
    at('/tasks')
    render(() => <PageMenu />)
    openMenu()

    expect(screen.queryByRole('menuitem', { name: /settings/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /open trash can/i })).not.toBeInTheDocument()
  })
})
