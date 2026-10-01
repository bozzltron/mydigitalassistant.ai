import { render, fireEvent, cleanup, screen } from '@solidjs/testing-library'
import { describe, it, expect, afterEach, vi } from 'vitest'
import ExternalLinkGuard from './ExternalLinkGuard'

const added: HTMLAnchorElement[] = []

afterEach(() => {
  added.forEach((a) => a.remove())
  added.length = 0
  cleanup()
})

function clickAnchor(href: string) {
  const a = document.createElement('a')
  a.setAttribute('href', href)
  a.target = '_blank'
  a.textContent = 'link'
  document.body.appendChild(a)
  added.push(a)
  fireEvent.click(a)
  return a
}

describe('ExternalLinkGuard', () => {
  it('asks before opening an off-origin link', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(() => <ExternalLinkGuard />)

    clickAnchor('https://example.com/story')

    expect(document.querySelector('.modal-overlay')).not.toBeNull()
    expect(screen.getByText('example.com')).toBeInTheDocument()
    expect(open).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it('opens the link in a new tab only after confirming', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(() => <ExternalLinkGuard />)

    clickAnchor('https://example.com/story')
    fireEvent.click(screen.getByText('Open'))

    expect(open).toHaveBeenCalledWith('https://example.com/story', '_blank', 'noopener,noreferrer')
    expect(document.querySelector('.modal-overlay')).toBeNull()
    open.mockRestore()
  })

  it('does not open when cancelled', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(() => <ExternalLinkGuard />)

    clickAnchor('https://example.com/story')
    fireEvent.click(screen.getByText('Cancel'))

    expect(open).not.toHaveBeenCalled()
    expect(document.querySelector('.modal-overlay')).toBeNull()
    open.mockRestore()
  })

  it('leaves same-origin and non-http links alone', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(() => <ExternalLinkGuard />)

    clickAnchor('/brain')
    clickAnchor('mailto:someone@example.com')

    expect(document.querySelector('.modal-overlay')).toBeNull()
    expect(open).not.toHaveBeenCalled()
    open.mockRestore()
  })
})
