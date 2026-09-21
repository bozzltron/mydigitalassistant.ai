import { createSignal, Show } from 'solid-js'
import { getYouTubeEmbedUrl, getYouTubeThumbnailUrl, extractYouTubeId } from '../../utils/media'

interface MediaCardProps {
  media: {
    type: 'image' | 'video' | 'youtube'
    url: string
    thumbnail?: string
    title?: string
    description?: string
    sourceUrl?: string
    aspectRatio?: number
  }
  onOpenLightbox?: (media: MediaCardProps['media'], index: number, allMedia: MediaCardProps['media'][]) => void
}

export default function MediaCard(props: MediaCardProps) {
  const { media, onOpenLightbox } = props
  const [imageError, setImageError] = createSignal(false)

  const handleImageError = () => {
    setImageError(true)
  }

  const handleImageLoad = () => {
    setImageError(false)
  }

  const handleClick = () => {
    if (onOpenLightbox && media.type === 'image') {
      onOpenLightbox(media, 0, [media])
    }
  }

  if (media.type === 'youtube') {
    const videoId = extractYouTubeId(media.url)
    const thumbnail = media.thumbnail || (videoId ? getYouTubeThumbnailUrl(videoId) : '')
    const embedUrl = videoId ? getYouTubeEmbedUrl(videoId) : ''

    return (
      <div class="msg-video-embed" onClick={handleClick}>
        <div class="video-placeholder" data-embed-url={embedUrl}>
          <button class="play-button" aria-label="Play video">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
          </button>
          {thumbnail && (
            <img
              src={thumbnail}
              alt=""
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', zIndex: -1 }}
              aria-hidden="true"
            />
          )}
          {media.title && <div class="video-title">{media.title}</div>}
          {media.description && <div class="video-channel">{media.description}</div>}
        </div>
      </div>
    )
  }

  if (media.type === 'video') {
    return (
      <div class="msg-video-embed">
        <video
          controls
          preload="metadata"
          poster={media.thumbnail}
        >
          <source src={media.url} type="video/mp4" />
          <source src={media.url} type="video/webm" />
          Your browser does not support the video tag.
        </video>
      </div>
    )
  }

  return (
    <div class="msg-media-hero" onClick={handleClick}>
      <Show when={!imageError()}>
        {() => (
          <img
            src={media.url}
            alt={media.title || ''}
            onError={handleImageError}
            onLoad={handleImageLoad}
            loading="lazy"
          />
        )}
      </Show>
      <Show when={imageError()}>
        {() => (
          <div class="msg-preview-card-placeholder" style={{ width: '100%', height: '100%', minHeight: '200px' }}>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <path d="M21 15l-5-5L5 21" />
            </svg>
          </div>
        )}
      </Show>
      {(media.title || media.description || media.sourceUrl) && (
        <div class="media-hero-caption">
          {media.title && <div style={{ fontWeight: 500 }}>{media.title}</div>}
          {media.description && <div style={{ fontSize: '0.75rem', opacity: 0.8 }}>{media.description}</div>}
          {media.sourceUrl && (
            <a href={media.sourceUrl} target="_blank" rel="noopener" style={{ fontSize: '0.7rem', marginTop: '4px', display: 'inline-block' }}>
              {new URL(media.sourceUrl).hostname}
            </a>
          )}
        </div>
      )}
    </div>
  )
}