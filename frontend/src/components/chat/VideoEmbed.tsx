import { createSignal } from 'solid-js'
import { getYouTubeEmbedUrl, getYouTubeThumbnailUrl } from '../../utils/media'

interface VideoEmbedProps {
  videoId: string
  title?: string
  channelTitle?: string
  thumbnailUrl?: string
  onOpenLightbox?: () => void
}

export default function VideoEmbed(props: VideoEmbedProps) {
  const { videoId, title, channelTitle, thumbnailUrl, onOpenLightbox: _onOpenLightbox } = props
  const [iframeLoaded, setIframeLoaded] = createSignal(false)
  const [showPlaceholder, setShowPlaceholder] = createSignal(true)

  const embedUrl = getYouTubeEmbedUrl(videoId)
  const thumbUrl = thumbnailUrl || getYouTubeThumbnailUrl(videoId)

  const handlePlaceholderClick = () => {
    setShowPlaceholder(false)
    setTimeout(() => setIframeLoaded(true), 100)
  }

  const handleIframeLoad = () => {
    setIframeLoaded(true)
    setShowPlaceholder(false)
  }

  return (
    <div class="msg-video-embed">
      <div class="video-placeholder" style={{ display: showPlaceholder() ? 'flex' : 'none' }} onClick={handlePlaceholderClick}>
        {thumbUrl && (
          <img
            src={thumbUrl}
            alt=""
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', zIndex: -1 }}
            aria-hidden="true"
          />
        )}
        <button class="play-button" aria-label="Play video">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>
        {title && <div class="video-title">{title}</div>}
        {channelTitle && <div class="video-channel">{channelTitle}</div>}
      </div>

      <iframe
        src={embedUrl}
        title={title || 'YouTube video'}
        frameBorder="0"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        allowFullScreen
        onLoad={handleIframeLoad}
        style={{ display: iframeLoaded() ? 'block' : 'none' }}
        loading="lazy"
      />
    </div>
  )
}