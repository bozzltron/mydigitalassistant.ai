import type { ChatMessage, MediaContent, YouTubeVideo, OgData, SearchInfo } from '../types/chat'

const YOUTUBE_URL_PATTERNS = [
  /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/shorts\/)([a-zA-Z0-9_-]{11})/,
  /youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})/,
]

const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v']
const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.bmp', '.svg']

export function extractYouTubeId(url: string): string | null {
  for (const pattern of YOUTUBE_URL_PATTERNS) {
    const match = url.match(pattern)
    if (match && match[1]) {
      return match[1]
    }
  }
  return null
}

export function isYouTubeUrl(url: string): boolean {
  return extractYouTubeId(url) !== null
}

export function isDirectVideoUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    const pathname = parsed.pathname.toLowerCase()
    return VIDEO_EXTENSIONS.some(ext => pathname.endsWith(ext))
  } catch {
    return false
  }
}

export function isDirectImageUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    const pathname = parsed.pathname.toLowerCase()
    return IMAGE_EXTENSIONS.some(ext => pathname.endsWith(ext))
  } catch {
    return false
  }
}

export function extractMediaFromMarkdown(content: string): MediaContent[] {
  const media: MediaContent[] = []
  const imageRegex = /!\[([^\]]*)\]\(([^)]+)\)/g
  let match

  while ((match = imageRegex.exec(content)) !== null) {
    const url = match[2]
    if (isYouTubeUrl(url)) {
      const videoId = extractYouTubeId(url)
      if (videoId) {
        media.push({
          type: 'youtube',
          url: `https://www.youtube.com/watch?v=${videoId}`,
          thumbnail: `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`,
        })
      }
    } else if (isDirectVideoUrl(url)) {
      media.push({
        type: 'video',
        url,
      })
    } else {
      media.push({
        type: 'image',
        url,
        title: match[1] || undefined,
      })
    }
  }

  return media
}

export function extractMediaFromOgData(ogData: Record<string, OgData>): MediaContent[] {
  const media: MediaContent[] = []

  for (const [url, data] of Object.entries(ogData)) {
    if (data && data.image && typeof data.image === 'string') {
      media.push({
        type: 'image',
        url: data.image,
        title: data.title,
        description: data.description,
        sourceUrl: url,
        aspectRatio: 16 / 9,
      })
    }
  }

  return media
}

export function extractMediaFromSearchInfo(searchInfo: SearchInfo): MediaContent[] {
  const media: MediaContent[] = []

  if (searchInfo?.results) {
    for (const result of searchInfo.results) {
      if (result.thumbnail && typeof result.thumbnail === 'string') {
        media.push({
          type: 'image',
          url: result.thumbnail,
          title: result.title,
          sourceUrl: result.url,
        })
      }
    }
  }

  if (searchInfo?.video_results) {
    for (const video of searchInfo.video_results) {
      if (video.thumbnailUrl && typeof video.thumbnailUrl === 'string') {
        media.push({
          type: 'youtube',
          url: video.url || `https://www.youtube.com/watch?v=${video.videoId}`,
          thumbnail: video.thumbnailUrl,
          title: video.title,
          description: video.channelTitle,
        })
      }
    }
  }

  return media
}

export function extractMediaFromSlots(slots: Array<{ value: string }>): MediaContent[] {
  const media: MediaContent[] = []

  for (const slot of slots) {
    if (slot.value && typeof slot.value === 'string') {
      if (isYouTubeUrl(slot.value)) {
        const videoId = extractYouTubeId(slot.value)
        if (videoId) {
          media.push({
            type: 'youtube',
            url: slot.value,
            thumbnail: `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`,
          })
        }
      } else if (isDirectImageUrl(slot.value)) {
        media.push({
          type: 'image',
          url: slot.value,
        })
      } else if (isDirectVideoUrl(slot.value)) {
        media.push({
          type: 'video',
          url: slot.value,
        })
      }
    }
  }

  return media
}

export function extractAllMedia(message: ChatMessage): MediaContent[] {
  const media: MediaContent[] = []

  if (message.meta?.ogData) {
    media.push(...extractMediaFromOgData(message.meta.ogData))
  }

  if (message.meta?.search_info) {
    media.push(...extractMediaFromSearchInfo(message.meta.search_info))
  }

  if (message.meta?.extraction_summary?.slots) {
    media.push(...extractMediaFromSlots(message.meta.extraction_summary.slots))
  }

  if (message.meta?.search_extraction_summary?.slots) {
    media.push(...extractMediaFromSlots(message.meta.search_extraction_summary.slots))
  }

  media.push(...extractMediaFromMarkdown(message.content))

  const seen = new Set<string>()
  return media.filter(m => {
    const key = m.url
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function getHeroMedia(media: MediaContent[]): MediaContent | null {
  if (media.length === 0) return null

  const videoMedia = media.find(m => m.type === 'video' || m.type === 'youtube')
  if (videoMedia) return videoMedia

  return media[0]
}

export function getGridMedia(media: MediaContent[], hero?: MediaContent | null): MediaContent[] {
  if (media.length <= 1) return []

  const heroUrl = hero?.url
  return media.filter(m => m.url !== heroUrl && m.type === 'image')
}

export function getPreviewCards(media: MediaContent[]): MediaContent[] {
  return media.filter(m => m.type === 'preview-card' || (m.type === 'image' && m.sourceUrl))
}

export function createYouTubeVideoFromId(videoId: string, title: string = ''): YouTubeVideo {
  return {
    videoId,
    title,
    thumbnailUrl: `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`,
    url: `https://www.youtube.com/watch?v=${videoId}`,
  }
}

export function getYouTubeEmbedUrl(videoId: string): string {
  return `https://www.youtube-nocookie.com/embed/${videoId}?rel=0&modestbranding=1&enablejsapi=1`
}

export function getYouTubeThumbnailUrl(videoId: string, quality: 'default' | 'mq' | 'hq' | 'sd' | 'maxres' = 'maxres'): string {
  const qualityMap = {
    default: 'default',
    mq: 'mqdefault',
    hq: 'hqdefault',
    sd: 'sddefault',
    maxres: 'maxresdefault',
  }
  return `https://img.youtube.com/vi/${videoId}/${qualityMap[quality]}.jpg`
}