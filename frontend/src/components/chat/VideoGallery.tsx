import { createSignal, createMemo, For, Show } from 'solid-js'
import type { MediaContent } from '../../types/chat'
import { getYouTubeThumbnailUrl, extractYouTubeId, imageSrc } from '../../utils/media'
import MediaCard from './MediaCard'

/**
 * One embedded video plus thumbnails for the rest.
 *
 * A video response can carry many results, and embedding every one loads an
 * iframe per result. Only the active video is embedded; the others are
 * thumbnails that swap into the single player, so there is never more than one
 * live iframe.
 */
export default function VideoGallery(props: { videos: MediaContent[] }) {
  const [active, setActive] = createSignal(0)

  // Clamp so a shrinking list can never index past the end.
  const activeIndex = createMemo(() =>
    Math.min(active(), Math.max(0, props.videos.length - 1))
  )
  const activeVideo = createMemo(() => props.videos[activeIndex()])
  const others = createMemo(() =>
    props.videos
      .map((video, index) => ({ video, index }))
      .filter((entry) => entry.index !== activeIndex())
  )

  const thumbSrc = (video: MediaContent) => {
    if (video.thumbnail) return imageSrc(video.thumbnail)
    const id = extractYouTubeId(video.url)
    return id ? getYouTubeThumbnailUrl(id, 'hq') : ''
  }

  return (
    <>
      <Show when={activeVideo()}>
        <MediaCard media={activeVideo()!} />
      </Show>

      <Show when={others().length > 0}>
        <div class="msg-video-thumbs" role="list" aria-label="More videos">
          <For each={others()}>
            {(entry) => (
              <button
                type="button"
                class="msg-video-thumb"
                role="listitem"
                title={entry.video.title || undefined}
                aria-label={entry.video.title ? `Play ${entry.video.title}` : 'Play video'}
                onClick={() => setActive(entry.index)}
              >
                <img src={thumbSrc(entry.video)} alt={entry.video.title || ''} loading="lazy" />
                <span class="msg-video-thumb-play" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                </span>
              </button>
            )}
          </For>
        </div>
      </Show>
    </>
  )
}
