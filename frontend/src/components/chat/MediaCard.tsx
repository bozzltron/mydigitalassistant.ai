import { createMemo, createSignal, createEffect, on, Show } from 'solid-js'
import type { MediaContent } from '../../types/chat'
import { getYouTubeEmbedUrl, extractYouTubeId, hostnameOf, imageSrc } from '../../utils/media'

interface MediaCardProps {
  media: MediaContent
}

export default function MediaCard(props: MediaCardProps) {
  const [imageError, setImageError] = createSignal(false)
  // Try the full-size image first; many source hosts block hotlinking, so fall
  // back to the (reliable) thumbnail before giving up on a placeholder.
  const [useThumbFallback, setUseThumbFallback] = createSignal(false)

  // Reset the fallback state whenever the media changes.
  createEffect(
    on(
      () => props.media,
      () => {
        setUseThumbFallback(false)
        setImageError(false)
      },
      { defer: true }
    )
  )

  const displaySrc = () => {
    const m = props.media
    if (useThumbFallback()) return imageSrc(m.thumbnail ?? m.url)
    return imageSrc(m.fullUrl ?? m.url)
  }

  // Read through props rather than destructuring it. A destructure snapshots the
  // value at component-creation time, so a later change to the `media` prop
  // would never reach the rendered branch. MessageContent passes a memo here,
  // so a change is possible in principle.
  const isYoutube = createMemo(() => props.media.type === 'youtube')
  const isVideo = createMemo(() => props.media.type === 'video')

  const videoId = createMemo(() => extractYouTubeId(props.media.url))
  const embedUrl = createMemo(() => (videoId() ? getYouTubeEmbedUrl(videoId()!) : ''))

  const handleImageError = () => {
    const m = props.media
    const full = m.fullUrl ?? m.url
    if (!useThumbFallback() && m.thumbnail && m.thumbnail !== full) {
      setUseThumbFallback(true)
      return
    }
    setImageError(true)
  }

  const handleImageLoad = () => {
    setImageError(false)
  }

  // One branch per media type, selected by <Show> rather than an early return.
  // A Solid component body runs once, so the previous `if (media.type === ...)`
  // returns were evaluated a single time and the card could never switch branch
  // if its prop changed.
  return (
    <>
      <Show when={isYoutube()}>
        <div class="msg-video-embed">
          <iframe
            src={embedUrl()}
            title={props.media.title || 'YouTube video'}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowfullscreen
            loading="lazy"
          />
        </div>
      </Show>

      <Show when={isVideo()}>
        <div class="msg-video-embed">
          <video
            controls
            preload="metadata"
            poster={imageSrc(props.media.thumbnail)}
          >
            <source src={props.media.url} type="video/mp4" />
            <source src={props.media.url} type="video/webm" />
            Your browser does not support the video tag.
          </video>
        </div>
      </Show>

      <Show when={!isYoutube() && !isVideo()}>
        <div class="msg-media-hero">
          <Show when={!imageError()}>
            <img
              src={displaySrc()}
              alt={props.media.title || ''}
              onError={handleImageError}
              onLoad={handleImageLoad}
              loading="lazy"
            />
          </Show>
          <Show when={imageError()}>
            <div class="msg-preview-card-placeholder">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                <circle cx="8.5" cy="8.5" r="1.5" />
                <path d="M21 15l-5-5L5 21" />
              </svg>
            </div>
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
                  {hostnameOf(props.media.sourceUrl)}
                </a>
              )}
            </div>
          )}
        </div>
      </Show>
    </>
  )
}
