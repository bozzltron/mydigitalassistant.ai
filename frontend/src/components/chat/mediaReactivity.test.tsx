import { render, screen, fireEvent, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach } from 'vitest'
import { createSignal } from 'solid-js'
import MediaCard from './MediaCard'
import MediaGrid from './MediaGrid'
import PreviewCards from './PreviewCards'

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

  it('switches branch when the media prop changes to a youtube embed (regression: a second early return)', () => {
    const [media, setMedia] = createSignal(image)
    render(() => <MediaCard media={media()} />)

    setMedia(youtube)

    // The youtube branch renders a placeholder carrying the derived embed URL.
    // Asserted loosely on the id: the exact host and query string are
    // getYouTubeEmbedUrl's business, not what this test is pinning.
    const placeholder = document.querySelector('.video-placeholder')
    expect(placeholder).not.toBeNull()
    expect(placeholder!.getAttribute('data-embed-url')).toContain('dQw4w9WgXcQ')
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

describe('PreviewCards reactivity', () => {
  const card = (host: string) => ({
    type: 'image' as const,
    url: `https://${host}/page`,
    sourceUrl: `https://${host}/page`,
    thumbnail: `https://${host}/thumb.png`,
  })

  it('follows a changed cards prop (regression: <For each={cards}> received a frozen array)', () => {
    const [cards, setCards] = createSignal([card('one.example')])
    render(() => <PreviewCards cards={cards()} />)
    expect(document.querySelectorAll('.msg-preview-card')).toHaveLength(1)

    setCards([card('one.example'), card('two.example')])

    expect(document.querySelectorAll('.msg-preview-card')).toHaveLength(2)
  })
})

describe('MediaGrid reactivity', () => {
  it('follows a changed media prop (regression: imageMedia was memoised from a destructured snapshot)', () => {
    const [media, setMedia] = createSignal([{ type: 'image' as const, url: 'https://example.com/a.png' }])
    render(() => <MediaGrid media={media()} />)
    expect(document.querySelectorAll('.msg-media-grid-item')).toHaveLength(1)

    setMedia([
      { type: 'image' as const, url: 'https://example.com/a.png' },
      { type: 'image' as const, url: 'https://example.com/b.png' },
    ])

    expect(document.querySelectorAll('.msg-media-grid-item')).toHaveLength(2)
  })
})

describe('MediaGrid lightbox', () => {
  it('opens the internal lightbox when no external handler is provided', () => {
    // Regression: Message used to pass a no-op onOpenLightbox, so MediaGrid
    // always took the "handled externally" branch and the lightbox never opened.
    render(() => <MediaGrid media={[{ type: 'image' as const, url: 'https://example.com/a.png' }]} />)

    fireEvent.click(document.querySelector('.msg-media-grid-item')!)

    expect(document.querySelector('.media-lightbox')).not.toBeNull()
  })

  it('survives the media list shrinking while open and closes when it empties', () => {
    const [media, setMedia] = createSignal([
      { type: 'image' as const, url: 'https://example.com/a.png' },
      { type: 'image' as const, url: 'https://example.com/b.png' },
    ])
    render(() => <MediaGrid media={media()} />)

    fireEvent.click(document.querySelectorAll('.msg-media-grid-item')[1] as Element)
    expect(document.querySelector('.media-lightbox')).not.toBeNull()

    // Shrinking must not read past the end of the list.
    setMedia([{ type: 'image' as const, url: 'https://example.com/a.png' }])
    expect(document.querySelector('.media-lightbox')).not.toBeNull()

    setMedia([])
    expect(document.querySelector('.media-lightbox')).toBeNull()
  })
})
