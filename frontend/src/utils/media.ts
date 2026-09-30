import type { ChatMessage, MediaContent, SearchResultItem } from '../types/chat'

const YOUTUBE_URL_PATTERNS = [
  /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/shorts\/)([a-zA-Z0-9_-]{11})/,
  /youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})/,
]

/** Coerce a wire value that should be a URL/string to a safe string. */
function asStr(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

export function extractYouTubeId(url: string): string | null {
  // Defensive: a search backend may hand us a non-string (e.g. Brave returns
  // `thumbnail`/`image` as objects), and `url.match` would throw.
  if (typeof url !== 'string' || !url) return null
  for (const pattern of YOUTUBE_URL_PATTERNS) {
    const match = url.match(pattern)
    if (match && match[1]) {
      return match[1]
    }
  }
  return null
}

/**
 * Route an image URL through the backend proxy so the browser never contacts
 * third-party image hosts directly (which would leak the user's IP). Non-http
 * URLs are returned unchanged.
 */
export function imageSrc(url: string | undefined | null): string {
  if (typeof url !== 'string' || !url) return ''
  if (!/^https?:\/\//i.test(url)) return url
  return `/image-proxy?url=${encodeURIComponent(url)}`
}

/**
 * Best-effort hostname for display. Returns `''` when the URL is missing or not
 * absolute/parseable rather than throwing: `new URL()` in a render path took the
 * whole message tree down on a relative or malformed `sourceUrl` from search or
 * OG data.
 */
export function hostnameOf(url: string | undefined | null): string {
  if (!url) return ''
  try {
    return new URL(url).hostname
  } catch {
    return ''
  }
}

/** Best-effort pathname for display; `''` when the URL cannot be parsed. */
export function pathnameOf(url: string | undefined | null, maxLength = 50): string {
  if (!url) return ''
  try {
    return new URL(url).pathname.slice(0, maxLength)
  } catch {
    return ''
  }
}

/**
 * Media for a message, drawn ONLY from its search results.
 *
 * Search is the only source of imagery: we do not store images, so nothing is
 * rendered from memory, slots, or markdown. Returns `[]` for non-search turns.
 *
 * Each image keeps both URLs: `thumbnail` (small, for the grid tile) and `url`
 * (the full image, for the hero and the lightbox).
 */
export function searchMedia(message: ChatMessage): MediaContent[] {
  const info = message.meta?.search_info
  if (!info) return []

  const media: MediaContent[] = []

  for (const result of info.results ?? []) {
    // Coerce: a result's thumbnail/image may arrive as an object
    // ({src, original}) from a backend that has not normalised it yet.
    // `thumbnail` is Brave's own CDN copy (reliable); `image` is the source
    // site's image, which is frequently hotlink-blocked.
    const preview = asStr(result.thumbnail)
    const full = asStr(result.image)
    const display = preview ?? full
    if (!display) continue
    media.push({
      type: 'image',
      url: display,
      fullUrl: full && full !== display ? full : undefined,
      thumbnail: preview,
      title: asStr(result.title),
      sourceUrl: asStr(result.url),
    })
  }

  for (const video of info.video_results ?? []) {
    const direct = asStr(video.url)
    const videoId = asStr(video.video_id)
    const url = direct ?? (videoId ? `https://www.youtube.com/watch?v=${videoId}` : undefined)
    if (!url) continue
    media.push({
      type: 'youtube',
      url,
      thumbnail: asStr(video.thumbnail_url),
      title: asStr(video.title),
      description: asStr(video.channel_title),
    })
  }

  const seen = new Set<string>()
  return media.filter((m) => {
    if (!m.url || seen.has(m.url)) return false
    seen.add(m.url)
    return true
  })
}

/**
 * The card at the top: the first image (images are pushed before videos in
 * `searchMedia`), else the first video when there are no images.
 */
export function getHeroMedia(media: MediaContent[]): MediaContent | null {
  if (media.length === 0) return null
  return media[0] ?? null
}

/** Images other than the hero; `MediaGrid` caps how many it displays. */
export function getGridMedia(media: MediaContent[], hero?: MediaContent | null): MediaContent[] {
  const heroUrl = hero?.url
  return media.filter((m) => m.type === 'image' && m.url !== heroUrl)
}

/** Videos other than the hero (rare; usually there is at most one). */
export function getExtraVideos(media: MediaContent[], hero?: MediaContent | null): MediaContent[] {
  const heroUrl = hero?.url
  return media.filter(
    (m) => (m.type === 'youtube' || m.type === 'video') && m.url !== heroUrl
  )
}

/** Results with no image or video, rendered as a compact source list. */
export function getSourceResults(message: ChatMessage): SearchResultItem[] {
  const results = message.meta?.search_info?.results ?? []
  return results.filter((r) => !r.thumbnail && !r.image)
}

export function getYouTubeEmbedUrl(videoId: string): string {
  return `https://www.youtube-nocookie.com/embed/${videoId}?rel=0&modestbranding=1&enablejsapi=1`
}

export function getYouTubeThumbnailUrl(
  videoId: string,
  quality: 'default' | 'mq' | 'hq' | 'sd' | 'maxres' = 'maxres'
): string {
  const qualityMap = {
    default: 'default',
    mq: 'mqdefault',
    hq: 'hqdefault',
    sd: 'sddefault',
    maxres: 'maxresdefault',
  }
  return `https://img.youtube.com/vi/${videoId}/${qualityMap[quality]}.jpg`
}
