import type { ChatMessage, MediaContent } from '../types/chat'

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


// Only surface video results when the query actually asks for video. Brave
// attaches a `videos` section to many web responses (no extra request), but
// showing it for "pics of Nick Cage" is noise.
const VIDEO_INTENT = /\b(videos?|watch|clips?|trailers?|youtube|vlogs?|music)\b/i

// When the query is about branding, keep logo/icon imagery; otherwise drop it.
const WANTS_BRAND_IMAGE = /\b(logos?|icons?|branding|brand|favicon|emblem)\b/i

// Tokens that mark an image as site chrome (logo, icon, ad, tracking pixel)
// rather than content. Tokens must be delimited so "advertising" or "iconic"
// are not caught.
const JUNK_IMAGE_TOKEN =
  /(^|[/_.-])(logos?|icons?|favicons?|sprite|brand|branding|badge|avatar|placeholder|blank|spacer|pixel|advert|advertisement|sponsor|banner|ads?|tracking|1x1)([/_.-]|$)/i
// Hosts that serve branding or ad assets.
const JUNK_IMAGE_HOST =
  /(^|\.)(clearbit\.com|icons8\.com|brandfetch\.io|gravatar\.com|doubleclick\.net|googlesyndication\.com|adsystem\.com|adservice\.google\.com)$/i

/**
 * True when an image is site chrome (logo/icon/ad) rather than meaningful
 * content. Uses the source image URL (Brave's `thumbnail.original`) plus the
 * page URL; images served from a site's homepage are treated as branding.
 */
function isJunkImage(imageUrl: string | undefined, resultUrl: string | undefined): boolean {
  const img = (imageUrl || '').toLowerCase()
  if (!img) return true
  // SVGs are almost always logos/diagrams for a web result.
  if (/\.svg(\?|#|$)/.test(img)) return true
  if (/s2\/favicons|favicon\./.test(img)) return true
  try {
    const url = new URL(img)
    if (JUNK_IMAGE_HOST.test(url.hostname)) return true
    if (JUNK_IMAGE_TOKEN.test(url.pathname)) return true
  } catch {
    if (JUNK_IMAGE_TOKEN.test(img)) return true
  }
  // A page with no path is a homepage; its image is usually the site logo.
  if (resultUrl) {
    try {
      const page = new URL(resultUrl)
      if (page.pathname === '' || page.pathname === '/') return true
    } catch {
      // Unparseable page URL: leave the image decision to the checks above.
    }
  }
  return false
}

/**
 * Media for a message, drawn ONLY from its search results.
 *
 * Search is the only source of imagery: we do not store images, so nothing is
 * rendered from memory, slots, or markdown. Returns `[]` for non-search turns.
 *
 * Each image keeps both URLs: `thumbnail` (Brave's small, reliable CDN copy) and
 * `url` (the full image, which is what the hero prefers and what it falls back
 * from when a source host blocks the fetch).
 */
export function searchMedia(message: ChatMessage): MediaContent[] {
  const info = message.meta?.search_info
  if (!info) return []

  const query = info.query || ''
  const wantsBrand = WANTS_BRAND_IMAGE.test(query)
  const wantsVideo = VIDEO_INTENT.test(query)

  const images: MediaContent[] = []
  for (const result of info.results ?? []) {
    // Coerce: a result's thumbnail/image may arrive as an object
    // ({src, original}) from a backend that has not normalised it yet.
    // `thumbnail` is Brave's own CDN copy (reliable); `image` is the source
    // site's image, which is frequently hotlink-blocked.
    const preview = asStr(result.thumbnail)
    const full = asStr(result.image)
    const display = preview ?? full
    if (!display) continue
    // Skip logos, icons and ad imagery unless the query is about branding.
    if (!wantsBrand && isJunkImage(full ?? preview, asStr(result.url))) continue
    images.push({
      type: 'image',
      url: display,
      fullUrl: full && full !== display ? full : undefined,
      thumbnail: preview,
      title: asStr(result.title),
      sourceUrl: asStr(result.url),
    })
  }

  const videos: MediaContent[] = []
  if (wantsVideo) {
    for (const video of info.video_results ?? []) {
      const direct = asStr(video.url)
      const videoId = asStr(video.video_id)
      const url = direct ?? (videoId ? `https://www.youtube.com/watch?v=${videoId}` : undefined)
      if (!url) continue
      videos.push({
        type: 'youtube',
        url,
        thumbnail: asStr(video.thumbnail_url),
        title: asStr(video.title),
        description: asStr(video.channel_title),
      })
    }
  }

  // A video query leads with the video — the video *is* the answer, and the
  // stills are supporting material. Everything else leads with the image.
  const media = wantsVideo ? [...videos, ...images] : [...images, ...videos]

  const seen = new Set<string>()
  return media.filter((m) => {
    if (!m.url || seen.has(m.url)) return false
    seen.add(m.url)
    return true
  })
}

/**
 * The card at the top: for a video query the first video (a video result is
 * the answer), otherwise the best image.
 *
 * "Best" prefers a distinct full-resolution image: Brave's preview is only
 * ~200px, and the hero renders at the message's full width, so a preview
 * upscaled into that slot is blurry (measured: previews are always ~200px;
 * ~76% of results carry a distinct full image at 1200-2048px).
 */
export function getHeroMedia(media: MediaContent[]): MediaContent | null {
  const first = media[0]
  if (!first) return null
  if (first.type === 'youtube' || first.type === 'video') return first
  return media.find((m) => m.fullUrl) ?? first
}

export function getYouTubeEmbedUrl(videoId: string): string {
  return `https://www.youtube-nocookie.com/embed/${videoId}?rel=0&modestbranding=1&enablejsapi=1`
}
