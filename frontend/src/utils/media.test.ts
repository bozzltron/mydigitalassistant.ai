import { describe, it, expect } from 'vitest'
import { searchMedia, getHeroMedia, imageSrc, extractYouTubeId } from './media'
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
      // Display URL is the reliable CDN thumbnail; the source image is kept for
      // the hero, which falls back to the thumbnail if the full image is blocked.
      url: 'https://img/a-s.png',
      thumbnail: 'https://img/a-s.png',
      fullUrl: 'https://img/a-o.png',
      sourceUrl: 'https://page/a',
    })
  })

  it('maps video results (snake_case wire) to youtube media', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'nick cage video',
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

  it('does not surface videos for an image-only query', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'pics of nick cage',
        results: [],
        video_results: [{ video_id: 'abcdefghijk', title: 'V', thumbnail_url: 'https://img/v.png' }],
      },
    })
    expect(searchMedia(m).some((x) => x.type === 'youtube')).toBe(false)
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

  it('ignores non-string thumbnail/image (regression: an object thumbnail crashed extractYouTubeId)', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'q',
        results: [
          // Pre-normalisation Brave shape.
          { title: 'A', url: 'https://page/a', snippet: '', engine: 'brave', thumbnail: { src: 's', original: 'o' } as unknown as string },
          { title: 'B', url: 'https://page/b', snippet: '', engine: 'brave', thumbnail: 'https://img/b.png' },
        ],
      },
    })
    const media = searchMedia(m)
    expect(media).toHaveLength(1)
    expect(media[0].url).toBe('https://img/b.png')
  })
})

describe('searchMedia image quality', () => {
  const withImage = (query: string, result: Record<string, unknown>) =>
    searchMedia(
      msg({
        search_info: {
          backend: 'brave',
          query,
          results: [{ title: 'A', url: 'https://news.example.com/story', snippet: '', engine: 'brave', ...result }],
        },
      })
    )

  it('drops logo/branding imagery from a news query', () => {
    expect(withImage('today news', { image: 'https://cdn.example.com/assets/logo.png' })).toHaveLength(0)
    expect(withImage('today news', { image: 'https://cdn.example.com/brand-mark.png' })).toHaveLength(0)
  })

  it('drops SVG imagery (logos/diagrams) from a web result', () => {
    expect(withImage('today news', { image: 'https://cdn.example.com/site/wordmark.svg' })).toHaveLength(0)
  })

  it('drops ad-network imagery', () => {
    expect(
      withImage('today news', { image: 'https://pagead2.googlesyndication.com/pagead/banner.jpg' })
    ).toHaveLength(0)
  })

  it('drops imagery served from a site homepage (usually branding)', () => {
    const media = searchMedia(
      msg({
        search_info: {
          backend: 'brave',
          query: 'today news',
          results: [
            {
              title: 'Example',
              url: 'https://example.com/',
              snippet: '',
              engine: 'brave',
              image: 'https://cdn.example.com/hero.jpg',
            },
          ],
        },
      })
    )
    expect(media).toHaveLength(0)
  })

  it('keeps a meaningful content image', () => {
    const media = withImage('machu picchu', {
      image: 'https://cdn.example.com/photos/machu-picchu-2024.jpg',
    })
    expect(media).toHaveLength(1)
    expect(media[0].type).toBe('image')
  })

  it('keeps logos when the query asks for branding', () => {
    const media = withImage('apple logo', { image: 'https://cdn.example.com/apple-logo.png' })
    expect(media).toHaveLength(1)
  })
})

describe('extractYouTubeId', () => {
  it('tolerates non-string input', () => {
    expect(extractYouTubeId(undefined as unknown as string)).toBeNull()
    expect(extractYouTubeId({} as unknown as string)).toBeNull()
    expect(extractYouTubeId('')).toBeNull()
  })
})

describe('composition', () => {
  // A search answer renders one hero and nothing else: no grid, no lightbox. So
  // the only thing `searchMedia` still has to get right is which item wins the
  // hero slot — the rest of the list is discarded by the renderer, not here.
  it('a video query heroes the video ahead of the images', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'machu picchu video',
        results: [
          { title: 'A', url: 'https://page/a', snippet: '', engine: 'brave', thumbnail: 'https://img/a.png' },
          { title: 'B', url: 'https://page/b', snippet: '', engine: 'brave', thumbnail: 'https://img/b.png' },
        ],
        video_results: [
          { video_id: 'abcdefghijk', title: 'V', thumbnail_url: 'https://img/v.png' },
          { video_id: 'lmnopqrstuv', title: 'W', thumbnail_url: 'https://img/w.png' },
        ],
      },
    })
    const media = searchMedia(m)
    expect(getHeroMedia(media)?.type).toBe('youtube')
    // The other video is still in the list — MessageContent renders it as a link.
    expect(media.filter((x) => x.type === 'youtube')).toHaveLength(2)
  })

  it('an image query heroes the image and surfaces no videos', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'machu picchu',
        results: [
          { title: 'A', url: 'https://page/a', snippet: '', engine: 'brave', thumbnail: 'https://img/a.png' },
          { title: 'B', url: 'https://page/b', snippet: '', engine: 'brave', thumbnail: 'https://img/b.png' },
        ],
        video_results: [{ video_id: 'abcdefghijk', title: 'V', thumbnail_url: 'https://img/v.png' }],
      },
    })
    const media = searchMedia(m)
    expect(getHeroMedia(media)?.type).toBe('image')
    expect(media.every((x) => x.type === 'image')).toBe(true)
  })

  it('getHeroMedia is null for an empty list, never undefined', () => {
    expect(getHeroMedia([])).toBeNull()
  })

  it('hero prefers a full-resolution image over a preview-only first result', () => {
    // Brave's preview is ~200px; the hero renders at message width, so a
    // preview-only first result would be a blurry banner. Prefer a later result
    // that carries a distinct full image.
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'guitar strings',
        results: [
          { title: 'A', url: 'https://page/a', snippet: '', engine: 'brave', thumbnail: 'https://img/a.png' },
          { title: 'B', url: 'https://page/b', snippet: '', engine: 'brave', thumbnail: 'https://img/b.png', image: 'https://img/b-full.jpg' },
        ],
        video_results: [],
      },
    })
    const hero = getHeroMedia(searchMedia(m))
    expect(hero?.fullUrl).toBe('https://img/b-full.jpg')
  })

  it('prefers Brave image-index results over web og:images', () => {
    const m = msg({
      search_info: {
        backend: 'brave',
        query: 'guitar strings',
        results: [
          { title: 'web', url: 'https://page/web', snippet: '', engine: 'brave', thumbnail: 'https://img/web200.png' },
        ],
        image_results: [
          { title: 'img', url: 'https://page/img', snippet: 'site.com', engine: 'brave-images', thumbnail: 'https://cdn/500.png', image: 'https://img/full.jpg' },
        ],
      },
    })
    const media = searchMedia(m)
    // The image-index result leads (it is query-relevant with a ~500px CDN copy).
    expect(media[0].thumbnail).toBe('https://cdn/500.png')
    expect(getHeroMedia(media)?.fullUrl).toBe('https://img/full.jpg')
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
