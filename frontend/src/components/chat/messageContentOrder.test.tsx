import { render, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach } from 'vitest'
import MessageContent from './MessageContent'
import type { ChatMessage } from '../../types/chat'

// The dynamic blocks a search answer can carry must always assemble in the same
// order — hero → body → other video links → sources — so the rhythm does not
// change with which blocks a given answer happens to have.

afterEach(cleanup)

const message = (meta: ChatMessage['meta']): ChatMessage => ({
  role: 'assistant',
  content: 'Body text\n\n**Sources:**\n\n- [A](https://a.example)',
  id: 'm',
  meta,
})

const childClasses = (container: HTMLElement) =>
  Array.from(container.querySelector('.message-content')!.children).map((el) =>
    (el as Element).className
  )

describe('MessageContent dynamic ordering', () => {
  it('renders hero → body → other video links → sources', () => {
    const m = message({
      search_info: {
        backend: 'brave',
        query: 'machu picchu video',
        results: [
          { title: 'A', url: 'https://page/a', snippet: '', engine: 'brave', thumbnail: 'https://img/a.png' },
          { title: 'B', url: 'https://page/b', snippet: '', engine: 'brave', thumbnail: 'https://img/b.png' },
        ],
        video_results: [
          { video_id: 'abcdefghijk', title: 'V1', thumbnail_url: 'https://img/v1.png' },
          { video_id: 'lmnopqrstuv', title: 'V2', thumbnail_url: 'https://img/v2.png' },
        ],
      },
    })

    const { container } = render(() => <MessageContent message={() => m} />)

    expect(childClasses(container)).toEqual([
      'msg-video-embed',
      'msg-markdown',
      'msg-video-links',
      'msg-markdown msg-sources-block',
    ])
  })

  it('renders an image hero when the query did not ask for video', () => {
    const m = message({
      search_info: {
        backend: 'brave',
        query: 'machu picchu',
        results: [
          { title: 'A', url: 'https://page/a', snippet: '', engine: 'brave', thumbnail: 'https://img/a.png' },
        ],
      },
    })

    const { container } = render(() => <MessageContent message={() => m} />)
    const classes = childClasses(container)

    expect(classes[0]).toContain('msg-media-hero')
    expect(classes).not.toContain('msg-video-links')
  })
})

// The grid and the lightbox were removed: the imagery was not good enough to
// earn a gallery, and one hero that is wrong is more wrong than six mediocre ones
// where the eye can skip past. These pin the absence, because a resurrected
// gallery would be invisible to the ordering assertions above.
describe('MessageContent renders exactly one image', () => {
  const manyImages = (count: number) =>
    message({
      search_info: {
        backend: 'brave',
        query: 'cute cats',
        results: Array.from({ length: count }, (_, i) => ({
          title: `C${i}`,
          url: `https://page/${i}`,
          snippet: '',
          engine: 'brave',
          thumbnail: `https://img/${i}.png`,
        })),
      },
    })

  it('renders a single hero and drops every other image', () => {
    const { container } = render(() => <MessageContent message={() => manyImages(8)} />)

    expect(container.querySelectorAll('.msg-media-hero')).toHaveLength(1)
    expect(container.querySelectorAll('img')).toHaveLength(1)
  })

  it('has no grid or lightbox left to open', () => {
    const { container } = render(() => <MessageContent message={() => manyImages(8)} />)

    expect(container.querySelector('.msg-media-grid')).toBeNull()
    expect(container.querySelector('.media-lightbox')).toBeNull()
    // One <img> total means the other seven images were not rendered as links
    // either — they reach the user through the Sources footer instead.
    expect(container.querySelectorAll('.message-content img')).toHaveLength(1)
  })
})

describe('MessageContent extra video links', () => {
  const twoVideos = () =>
    message({
      search_info: {
        backend: 'brave',
        query: 'machu picchu video',
        results: [],
        video_results: [
          { video_id: 'abcdefghijk', title: 'First video', thumbnail_url: 'https://img/v1.png' },
          { video_id: 'lmnopqrstuv', title: 'Second video', thumbnail_url: 'https://img/v2.png' },
        ],
      },
    })

  it('links the video that was not embedded, and does not link the embedded one', () => {
    const { container } = render(() => <MessageContent message={() => twoVideos()} />)

    const links = Array.from(container.querySelectorAll('.msg-video-links a'))
    expect(links).toHaveLength(1)
    expect(links[0].getAttribute('href')).toContain('lmnopqrstuv')
    expect(links[0].textContent?.trim()).toBe('Second video')

    // Exactly one embed, and it is not the one we just linked.
    const iframes = container.querySelectorAll('.msg-video-embed iframe')
    expect(iframes).toHaveLength(1)
    expect(iframes[0].getAttribute('src')).toContain('abcdefghijk')
  })

  it('renders no link list when there is only the one video', () => {
    const m = message({
      search_info: {
        backend: 'brave',
        query: 'machu picchu video',
        results: [],
        video_results: [{ video_id: 'abcdefghijk', title: 'Only video', thumbnail_url: 'https://img/v1.png' }],
      },
    })

    const { container } = render(() => <MessageContent message={() => m} />)
    expect(container.querySelector('.msg-video-links')).toBeNull()
  })
})
