import { render, screen, fireEvent, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach } from 'vitest'
import { createSignal } from 'solid-js'
import MediaCard from './MediaCard'
import MediaGrid from './MediaGrid'
import VideoGallery from './VideoGallery'

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

describe('MediaGrid cap', () => {
  it('caps visible tiles and shows a +N overflow tile', () => {
    const media = Array.from({ length: 9 }, (_, i) => ({
      type: 'image' as const,
      url: `https://example.com/${i}.png`,
    }))
    render(() => <MediaGrid media={media} maxVisible={6} />)

    // 5 tiles + 1 overflow tile.
    expect(document.querySelectorAll('.msg-media-grid-item')).toHaveLength(6)
    expect(document.querySelector('.grid-more-count')?.textContent).toBe('+3')
  })
})

describe('MediaGrid full-resolution tiles', () => {
  const withFull = {
    type: 'image' as const,
    url: 'https://cdn.example.com/thumb.jpg',
    thumbnail: 'https://cdn.example.com/thumb.jpg',
    fullUrl: 'https://origin.example.com/full.jpg',
  }

  it('renders the full-size image, not the grainy thumbnail', () => {
    render(() => <MediaGrid media={[withFull]} />)

    const img = document.querySelector('.msg-media-grid-item img') as HTMLImageElement
    expect(img.getAttribute('src')).toContain(
      encodeURIComponent('https://origin.example.com/full.jpg')
    )
  })

  it('falls back to the thumbnail when the full-size image fails to load', () => {
    render(() => <MediaGrid media={[withFull]} />)

    const img = document.querySelector('.msg-media-grid-item img') as HTMLImageElement
    expect(img.getAttribute('src')).toContain(
      encodeURIComponent('https://origin.example.com/full.jpg')
    )

    fireEvent.error(img)

    const after = document.querySelector('.msg-media-grid-item img') as HTMLImageElement
    expect(after.getAttribute('src')).toContain(
      encodeURIComponent('https://cdn.example.com/thumb.jpg')
    )
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

describe('MediaGrid tiles', () => {
  it('shows the image with no caption overlay (details live in the modal)', () => {
    render(() => (
      <MediaGrid
        media={[
          {
            type: 'image' as const,
            url: 'https://img/a.png',
            title: 'A title',
            sourceUrl: 'https://page/a',
          },
        ]}
      />
    ))

    expect(document.querySelector('.grid-item-overlay')).toBeNull()
    expect(document.querySelector('.msg-media-grid-item img')).not.toBeNull()
  })
})

describe('VideoGallery', () => {
  const videos = [
    { type: 'youtube' as const, url: 'https://youtube.com/watch?v=aaaaaaaaaaa', title: 'V1', thumbnail: 'https://img/v1.png' },
    { type: 'youtube' as const, url: 'https://youtube.com/watch?v=bbbbbbbbbbb', title: 'V2', thumbnail: 'https://img/v2.png' },
    { type: 'youtube' as const, url: 'https://youtube.com/watch?v=ccccccccccc', title: 'V3', thumbnail: 'https://img/v3.png' },
  ]

  it('embeds one video and shows the rest as thumbnails', () => {
    render(() => <VideoGallery videos={videos} />)

    const iframes = document.querySelectorAll('.msg-video-embed iframe')
    expect(iframes).toHaveLength(1)
    expect(iframes[0].getAttribute('src')).toContain('aaaaaaaaaaa')
    expect(document.querySelectorAll('.msg-video-thumb')).toHaveLength(2)
  })

  it('swaps the embedded video when a thumbnail is clicked', () => {
    render(() => <VideoGallery videos={videos} />)

    fireEvent.click(document.querySelectorAll('.msg-video-thumb')[1]!)

    const iframe = document.querySelector('.msg-video-embed iframe')
    expect(iframe!.getAttribute('src')).toContain('ccccccccccc')
    expect(document.querySelectorAll('.msg-video-thumb')).toHaveLength(2)
  })
})
