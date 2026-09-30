import { describe, it, expect } from 'vitest'
import {
  searchMedia,
  getHeroMedia,
  getGridMedia,
  getExtraVideos,
  getSourceResults,
  imageSrc,
} from './media'
import type { ChatMessage } from '../types/chat'

const msg = (meta: ChatMessage['meta']): ChatMessage => ({
  role: 'assistant',
  content: 'text',
  id: 'm',
  meta,
})

describe('searchMedia', () => {
  it('returns nothing without search_info (imagery is search-only)', () => {
    expect(searchMedia({ role: 'assistant', content: '![x](https://x/y.png)', id: 'm' })).toEqual([])
    expect(searchMedia(msg({}))).toEqual([])
  })

  it('builds images from result thumbnails, keeping preview and full URLs', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'q',
        results: [
          {
            title: 'A',
            url: 'https://page/a',
            snippet: '',
            engine: 'brave',
            thumbnail: 'https://img/a-s.png',
            image: 'https://img/a-o.png',
          },
        ],
      },
    })
    const media = searchMedia(m)
    expect(media).toHaveLength(1)
    expect(media[0]).toMatchObject({
      type: 'image',
      url: 'https://img/a-o.png',
      thumbnail: 'https://img/a-s.png',
      sourceUrl: 'https://page/a',
    })
  })

  it('maps video results (snake_case wire) to youtube media', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'q',
        results: [],
        video_results: [
          {
            video_id: 'abcdefghijk',
            title: 'V',
            channel_title: 'Chan',
            thumbnail_url: 'https://img/v.png',
            url: null,
          },
        ],
      },
    })
    expect(searchMedia(m)[0]).toMatchObject({
      type: 'youtube',
      url: 'https://www.youtube.com/watch?v=abcdefghijk',
      thumbnail: 'https://img/v.png',
      title: 'V',
      description: 'Chan',
    })
  })

  it('dedupes by url', () => {
    const r = {
      title: 'A',
      url: 'https://page/a',
      snippet: '',
      engine: 'brave',
      thumbnail: 'https://img/a.png',
      image: 'https://img/a.png',
    }
    const m = msg({ search_info: { backend: 'brave', query: 'q', results: [r, { ...r, title: 'B' }] } })
    expect(searchMedia(m)).toHaveLength(1)
  })
})

describe('composition', () => {
  it('hero prefers a video; grid holds the images; sources are results without media', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'q',
        results: [
          { title: 'A', url: 'https://page/a', snippet: '', engine: 'brave', thumbnail: 'https://img/a.png' },
          { title: 'B', url: 'https://page/b', snippet: '', engine: 'brave', thumbnail: 'https://img/b.png' },
          { title: 'C', url: 'https://page/c', snippet: '', engine: 'brave' },
        ],
        video_results: [{ video_id: 'abcdefghijk', title: 'V', thumbnail_url: 'https://img/v.png' }],
      },
    })
    const media = searchMedia(m)
    const hero = getHeroMedia(media)
    expect(hero?.type).toBe('youtube')
    expect(getGridMedia(media, hero)).toHaveLength(2)
    expect(getExtraVideos(media, hero)).toHaveLength(0)
    expect(getSourceResults(m).map((r) => r.title)).toEqual(['C'])
  })
})

describe('imageSrc', () => {
  it('routes http(s) through the backend proxy, leaves others alone', () => {
    expect(imageSrc('https://img/a.png')).toBe(
      '/image-proxy?url=' + encodeURIComponent('https://img/a.png')
    )
    expect(imageSrc('')).toBe('')
    expect(imageSrc(undefined)).toBe('')
    expect(imageSrc('/relative.png')).toBe('/relative.png')
  })
})
