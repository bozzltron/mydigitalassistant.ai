import { createMemo, createSignal, Show } from 'solid-js'
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
  const [imageError, setImageError] = createSignal(false)

  // Read through props rather than destructuring it. A destructure snapshots the
  // value at component-creation time, so a later change to the `media` prop
  // would never reach the rendered branch. MessageContent passes a memo here,
  // so a change is possible in principle.
  const isYoutube = createMemo(() => props.media.type === 'youtube')
  const isVideo = createMemo(() => props.media.type === 'video')

  const videoId = createMemo(() => extractYouTubeId(props.media.url))
  const thumbnail = createMemo(
    () => props.media.thumbnail || (videoId() ? getYouTubeThumbnailUrl(videoId()!) : '')
  )
  const embedUrl = createMemo(() => (videoId() ? getYouTubeEmbedUrl(videoId()!) : ''))

  const handleImageError = () => {
    setImageError(true)
  }

  const handleImageLoad = () => {
    setImageError(false)
  }

  const handleClick = () => {
    if (props.onOpenLightbox && props.media.type === 'image') {
      props.onOpenLightbox(props.media, 0, [props.media])
    }
  }

  // One branch per media type, selected by <Show> rather than an early return.
  // A Solid component body runs once, so the previous `if (media.type === ...)`
  // returns were evaluated a single time and the card could never switch branch
  // if its prop changed.
  return (
    <>
      <Show when={isYoutube()}>
        <div class="msg-video-embed" onClick={handleClick}>
          <div class="video-placeholder" data-embed-url={embedUrl()}>
            <button class="play-button" aria-label="Play video">
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5v14l11-7z" />
              </svg>
            </button>
            {thumbnail() && (
              <img
                src={thumbnail()!}
                alt=""
                class="video-cover-thumb"
                aria-hidden="true"
              />
            )}
            {props.media.title && <div class="video-title">{props.media.title}</div>}
            {props.media.description && <div class="video-channel">{props.media.description}</div>}
          </div>
        </div>
      </Show>

      <Show when={isVideo()}>
        <div class="msg-video-embed">
          <video
            controls
            preload="metadata"
            poster={props.media.thumbnail}
          >
            <source src={props.media.url} type="video/mp4" />
            <source src={props.media.url} type="video/webm" />
            Your browser does not support the video tag.
          </video>
        </div>
      </Show>

      <Show when={!isYoutube() && !isVideo()}>
        <div class="msg-media-hero" onClick={handleClick}>
          <Show when={!imageError()}>
            {() => (
              <img
                src={props.media.url}
                alt={props.media.title || ''}
                onError={handleImageError}
                onLoad={handleImageLoad}
                loading="lazy"
              />
            )}
          </Show>
          <Show when={imageError()}>
            {() => (
              <div class="msg-preview-card-placeholder">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                  <circle cx="8.5" cy="8.5" r="1.5" />
                  <path d="M21 15l-5-5L5 21" />
                </svg>
              </div>
            )}
          </Show>
          {(props.media.title || props.media.description || props.media.sourceUrl) && (
            <div class="media-hero-caption">
              {props.media.title && <div class="media-caption-title">{props.media.title}</div>}
              {props.media.description && (
                <div class="caption-description">{props.media.description}</div>
              )}
              {props.media.sourceUrl && (
                <a
                  class="caption-source"
                  href={props.media.sourceUrl}
                  target="_blank"
                  rel="noopener"
                >
                  {new URL(props.media.sourceUrl).hostname}
                </a>
              )}
            </div>
          )}
        </div>
      </Show>
    </>
  )
}
