import { render, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach } from 'vitest'
import MessageContent from './MessageContent'
import type { ChatMessage } from '../../types/chat'

// The dynamic blocks a search answer can carry must always assemble in the same
// order — hero → body → image grid → video thumbnails → sources — so the rhythm
// does not change with which blocks a given answer happens to have.

afterEach(cleanup)

const message = (meta: ChatMessage['meta']): ChatMessage => ({
  role: 'assistant',
  content: 'Body text\n\n**Sources:**\n\n- [A](https://a.example)',
  id: 'm',
  meta,
})

describe('MessageContent dynamic ordering', () => {
  it('renders hero → body → grid → video thumbnails → sources', () => {
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
    const classes = Array.from(container.querySelector('.message-content')!.children).map(
      (el) => (el as Element).className
    )

    expect(classes).toEqual([
      'msg-video-embed',
      'msg-video-thumbs',
      'msg-markdown',
      'msg-media-grid',
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
    const classes = Array.from(container.querySelector('.message-content')!.children).map(
      (el) => (el as Element).className
    )

    expect(classes[0]).toBe('msg-media-hero')
    expect(classes).not.toContain('msg-video-thumbs')
  })
})
