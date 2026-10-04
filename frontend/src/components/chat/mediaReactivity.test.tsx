import { render, screen, fireEvent, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach } from 'vitest'
import { createSignal } from 'solid-js'
import MediaCard from './MediaCard'

// Every test here targets the same defect: a component read its props by
// destructuring (`const { media } = props`) or returned early from the body.
// A Solid component body runs exactly once, so both freeze whatever the props
// held at creation time. The props are supplied as getters by the compiler, so
// a parent updating a signal reaches `props` but never reached the destructured
// copy. That is the bug these tests were written to pin.

afterEach(cleanup)

const image = { type: 'image' as const, url: 'https://example.com/a.png', title: 'a title' }
const youtube = { type: 'youtube' as const, url: 'https://youtube.com/watch?v=dQw4w9WgXcQ' }
const mp4 = { type: 'video' as const, url: 'https://example.com/clip.mp4' }

describe('MediaCard reactivity', () => {
  it('renders the image branch when the media prop starts as an image', () => {
    const [media] = createSignal(image)
    render(() => <MediaCard media={media()} />)
    expect(screen.getByAltText('a title')).toBeInTheDocument()
  })

  it('switches branch when the media prop changes to a video (regression: an early return fixed the branch at creation)', () => {
    const [media, setMedia] = createSignal(image)
    render(() => <MediaCard media={media()} />)
    expect(screen.getByAltText('a title')).toBeInTheDocument()

    setMedia(mp4)

    // The image element is gone and a <video> with the right source is present.
    expect(screen.queryByAltText('a title')).toBeNull()
    const video = document.querySelector('video')
    expect(video).not.toBeNull()
    expect(video!.querySelector('source[src="https://example.com/clip.mp4"]')).not.toBeNull()
  })

  it('switches branch when the media prop changes to a youtube embed', () => {
    const [media, setMedia] = createSignal(image)
    render(() => <MediaCard media={media()} />)

    setMedia(youtube)

    // The YouTube branch renders a real embed (an in-flow iframe).
    const iframe = document.querySelector('iframe')
    expect(iframe).not.toBeNull()
    expect(iframe!.getAttribute('src')).toContain('dQw4w9WgXcQ')
  })

  it('reflects a changed title on the same card instance', () => {
    const [media, setMedia] = createSignal(image)
    render(() => <MediaCard media={media()} />)
    expect(screen.getByAltText('a title')).toBeInTheDocument()

    setMedia({ ...image, title: 'a different title' })

    expect(screen.getByAltText('a different title')).toBeInTheDocument()
    expect(screen.queryByAltText('a title')).toBeNull()
  })
})

describe('MediaCard resolution', () => {
  const withFull = {
    type: 'image' as const,
    url: 'https://cdn.example.com/thumb.jpg',
    thumbnail: 'https://cdn.example.com/thumb.jpg',
    fullUrl: 'https://origin.example.com/full.jpg',
  }

  it('renders the full-size image, not the grainy thumbnail', () => {
    render(() => <MediaCard media={withFull} />)

    const img = document.querySelector('.msg-media-hero img') as HTMLImageElement
    expect(img.getAttribute('src')).toContain(
      encodeURIComponent('https://origin.example.com/full.jpg')
    )
  })

  it('falls back to the thumbnail when the full-size image fails to load', () => {
    render(() => <MediaCard media={withFull} />)

    const img = document.querySelector('.msg-media-hero img') as HTMLImageElement
    expect(img.getAttribute('src')).toContain(
      encodeURIComponent('https://origin.example.com/full.jpg')
    )

    fireEvent.error(img)

    const after = document.querySelector('.msg-media-hero img') as HTMLImageElement
    expect(after.getAttribute('src')).toContain(
      encodeURIComponent('https://cdn.example.com/thumb.jpg')
    )
  })

  it('resets the thumbnail fallback when the media prop changes', () => {
    const [media, setMedia] = createSignal(withFull)
    render(() => <MediaCard media={media()} />)

    fireEvent.error(document.querySelector('.msg-media-hero img') as HTMLImageElement)
    expect(
      (document.querySelector('.msg-media-hero img') as HTMLImageElement).getAttribute('src')
    ).toContain(encodeURIComponent('https://cdn.example.com/thumb.jpg'))

    setMedia({ ...withFull, url: 'https://cdn.example.com/other.jpg', fullUrl: 'https://origin.example.com/other.jpg' })

    // A new hero must try full resolution again rather than inherit the previous
    // image's failure — with one hero per answer, a stale flag shows a thumbnail
    // for the rest of the session.
    expect(
      (document.querySelector('.msg-media-hero img') as HTMLImageElement).getAttribute('src')
    ).toContain(encodeURIComponent('https://origin.example.com/other.jpg'))
  })

  it('gives up on the placeholder only after the thumbnail also fails', () => {
    render(() => <MediaCard media={withFull} />)

    fireEvent.error(document.querySelector('.msg-media-hero img') as HTMLImageElement)
    fireEvent.error(document.querySelector('.msg-media-hero img') as HTMLImageElement)

    expect(document.querySelector('.msg-media-hero img')).toBeNull()
    expect(document.querySelector('.msg-media-hero .msg-preview-card-placeholder')).not.toBeNull()
  })
})
