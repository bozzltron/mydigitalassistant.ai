import { createMemo, createSignal } from 'solid-js'
import { getYouTubeEmbedUrl, getYouTubeThumbnailUrl } from '../../utils/media'

interface VideoEmbedProps {
  videoId: string
  title?: string
  channelTitle?: string
  thumbnailUrl?: string
  onOpenLightbox?: () => void
}

export default function VideoEmbed(props: VideoEmbedProps) {
  const [iframeLoaded, setIframeLoaded] = createSignal(false)
  const [showPlaceholder, setShowPlaceholder] = createSignal(true)

  // Derived from props via memos rather than a destructure. The destructure
  // captured the values at creation time, so a new videoId or thumbnailUrl prop
  // would leave the previous video on screen.
  const embedUrl = createMemo(() => getYouTubeEmbedUrl(props.videoId))
  const thumbUrl = createMemo(
    () => props.thumbnailUrl || getYouTubeThumbnailUrl(props.videoId)
  )

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
      {/* Poster and iframe swap by class rather than an inline
          `display: flex | none` ternary. Both must stay mounted: the iframe
          needs to be in the DOM for its onLoad to fire, and the placeholder has
          to still be there to be clicked. `.msg-video-embed .is-hidden` lives in
          media-grid.css. */}
      <div
        class="video-placeholder"
        classList={{ 'is-hidden': !showPlaceholder() }}
        onClick={handlePlaceholderClick}
      >
        {thumbUrl() && (
          <img
            src={thumbUrl()!}
            alt=""
            class="video-cover-thumb"
            aria-hidden="true"
          />
        )}
        <button class="play-button" aria-label="Play video">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>
        {props.title && <div class="video-title">{props.title}</div>}
        {props.channelTitle && <div class="video-channel">{props.channelTitle}</div>}
      </div>

      <iframe
        src={embedUrl()}
        title={props.title || 'YouTube video'}
        frameBorder="0"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        allowFullScreen
        onLoad={handleIframeLoad}
        classList={{ 'is-hidden': !iframeLoaded() }}
        loading="lazy"
      />
    </div>
  )
}
