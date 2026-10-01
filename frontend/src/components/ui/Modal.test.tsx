import { render, fireEvent, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { Modal } from './Modal'

afterEach(cleanup)

describe('Modal', () => {
  it('closes on Escape while open (handleClose animates 150ms first)', async () => {
    const onClose = vi.fn()
    render(() => (
      <Modal isOpen onClose={onClose}>
        <p>body</p>
      </Modal>
    ))

    fireEvent.keyDown(document, { key: 'Escape' })
    await new Promise((r) => setTimeout(r, 200))

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores Escape while closed', () => {
    const onClose = vi.fn()
    render(() => (
      <Modal isOpen={false} onClose={onClose}>
        <p>body</p>
      </Modal>
    ))

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
  })
})
