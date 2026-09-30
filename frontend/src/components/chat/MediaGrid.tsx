import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js'
import type { MediaContent } from '../../types/chat'
import { hostnameOf, imageSrc } from '../../utils/media'

const DEFAULT_MAX_VISIBLE = 6

interface MediaGridProps {
  media: MediaContent[]
  /** Tiles shown before an overflow tile; the rest open in the lightbox. */
  maxVisible?: number
  onOpenLightbox?: (media: MediaContent, index: number, allMedia: MediaContent[]) => void
}

export default function MediaGrid(props: MediaGridProps) {
  const [lightboxOpen, setLightboxOpen] = createSignal(false)
  const [lightboxIndex, setLightboxIndex] = createSignal(0)
  const [loadedImages, setLoadedImages] = createSignal<Set<number>>(new Set())
  const [failedImages, setFailedImages] = createSignal<Set<number>>(new Set())

  const imageMedia = createMemo(() => props.media.filter((m) => m.type === 'image'))
  const maxVisible = () => props.maxVisible ?? DEFAULT_MAX_VISIBLE
  const hiddenCount = createMemo(() => Math.max(0, imageMedia().length - maxVisible()))
  // One slot is given to the "+N more" tile when there is overflow.
  const visibleMedia = createMemo(() =>
    hiddenCount() > 0 ? imageMedia().slice(0, maxVisible() - 1) : imageMedia()
  )
  const overflowIndex = () => maxVisible() - 1

  // The media prop can shrink between renders; close or clamp so the accessor
  // below can never index past the end.
  createEffect(() => {
    const count = imageMedia().length
    if (count === 0) {
      if (lightboxOpen()) closeLightbox()
      return
    }
    setLightboxIndex((i) => Math.min(i, count - 1))
  })

  const currentImage = createMemo(() => imageMedia()[lightboxIndex()])
  // Full-size in the lightbox, falling back to the thumbnail if the source
  // host blocks the fetch.
  const [lightboxThumb, setLightboxThumb] = createSignal(false)
  createEffect(() => {
    lightboxIndex()
    setLightboxThumb(false)
  })
  const lightboxSrc = () => {
    const item = currentImage()
    if (!item) return ''
    return lightboxThumb()
      ? imageSrc(item.thumbnail ?? item.url)
      : imageSrc(item.fullUrl ?? item.url)
  }
  const handleLightboxError = () => {
    const item = currentImage()
    if (
      item &&
      !lightboxThumb() &&
      item.thumbnail &&
      item.thumbnail !== (item.fullUrl ?? item.url)
    ) {
      setLightboxThumb(true)
    }
  }

  const openLightbox = (index: number) => {
    setLightboxIndex(index)
    setLightboxOpen(true)
    document.body.style.overflow = 'hidden'
  }

  const closeLightbox = () => {
    setLightboxOpen(false)
    document.body.style.overflow = ''
  }

  const goToPrev = () => {
    setLightboxIndex((i) => (i - 1 + imageMedia().length) % imageMedia().length)
  }

  const goToNext = () => {
    setLightboxIndex((i) => (i + 1) % imageMedia().length)
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (!lightboxOpen()) return
    if (e.key === 'Escape') closeLightbox()
    if (e.key === 'ArrowLeft') goToPrev()
    if (e.key === 'ArrowRight') goToNext()
  }

  createEffect(() => {
    if (lightboxOpen()) {
      document.addEventListener('keydown', handleKeyDown)
    }
  })

  onCleanup(() => {
    document.body.style.overflow = ''
    document.removeEventListener('keydown', handleKeyDown)
  })

  const markLoaded = (index: number) => {
    setLoadedImages((prev) => new Set(prev).add(index))
  }

  const markFailed = (index: number) => {
    setFailedImages((prev) => new Set(prev).add(index))
  }

  const handleGridItemClick = (index: number, e: Event) => {
    const media = imageMedia()
    const item = media[index]
    if (!item) return
    if (props.onOpenLightbox) {
      props.onOpenLightbox(item, index, media)
    } else {
      openLightbox(index)
    }
    e.preventDefault()
  }

  const getAspectRatioClass = (item: MediaContent) => {
    if (item.aspectRatio) {
      if (item.aspectRatio > 1.5) return 'msg-media-grid-item--wide'
      if (item.aspectRatio < 0.75) return 'msg-media-grid-item--tall'
    }
    if (item.width && item.height) {
      const ratio = item.width / item.height
      if (ratio > 1.5) return 'msg-media-grid-item--wide'
      if (ratio < 0.75) return 'msg-media-grid-item--tall'
    }
    return ''
  }

  return (
    <>
      <div class="msg-media-grid" role="list" aria-label="Image gallery">
        <For each={visibleMedia()}>
          {(item, index) => (
            <div
              class={['msg-media-grid-item', getAspectRatioClass(item)].join(' ')}
              role="listitem"
              tabindex="0"
              onClick={(e) => handleGridItemClick(index(), e)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  handleGridItemClick(index(), e)
                }
              }}
            >
              <Show
                when={!failedImages().has(index())}
                fallback={<div class="msg-media-grid-placeholder" aria-hidden="true" />}
              >
                <img
                  src={imageSrc(item.thumbnail ?? item.url)}
                  alt={item.title || ''}
                  loading="lazy"
                  onLoad={() => markLoaded(index())}
                  onError={() => markFailed(index())}
                  classList={{ 'is-loaded': loadedImages().has(index()) }}
                />
              </Show>
              <div class="grid-item-overlay">
                {item.title && <div class="grid-item-title">{item.title}</div>}
                {item.sourceUrl && (
                  <a
                    class="grid-item-source"
                    href={item.sourceUrl}
                    target="_blank"
                    rel="noopener"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {hostnameOf(item.sourceUrl)}
                  </a>
                )}
              </div>
              <button
                class="grid-item-expand"
                aria-label="View full size"
                onClick={(e) => {
                  e.stopPropagation()
                  handleGridItemClick(index(), e)
                }}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                </svg>
              </button>
            </div>
          )}
        </For>

        <Show when={hiddenCount() > 0}>
          <div
            class="msg-media-grid-item msg-media-grid-item--more"
            role="listitem"
            tabindex="0"
            onClick={(e) => handleGridItemClick(overflowIndex(), e)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                handleGridItemClick(overflowIndex(), e)
              }
            }}
          >
            <div class="grid-more">
              <span class="grid-more-count">+{hiddenCount()}</span>
              <span class="grid-more-label">more</span>
            </div>
          </div>
        </Show>
      </div>

      <Show when={lightboxOpen() && currentImage()}>
        <div class="media-lightbox" onClick={closeLightbox}>
          <button
            class="media-lightbox-close"
            onClick={(e) => { e.stopPropagation(); closeLightbox(); }}
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>

          {imageMedia().length > 1 && (
            <>
              <button
                class="media-lightbox-nav media-lightbox-nav--prev"
                onClick={(e) => { e.stopPropagation(); goToPrev(); }}
                aria-label="Previous image"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="15 18 9 12 15 6" />
                </svg>
              </button>
              <button
                class="media-lightbox-nav media-lightbox-nav--next"
                onClick={(e) => { e.stopPropagation(); goToNext(); }}
                aria-label="Next image"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>
            </>
          )}

          <div class="media-lightbox-content" onClick={(e) => e.stopPropagation()}>
            <img
              class="media-lightbox-image"
              src={lightboxSrc()}
              alt={currentImage()!.title || ''}
              onError={handleLightboxError}
            />
            {(currentImage()!.title || currentImage()!.sourceUrl) && (
              <div class="media-lightbox-caption">
                {currentImage()!.title && (
                  <div class="media-caption-title">{currentImage()!.title}</div>
                )}
                {currentImage()!.sourceUrl && (
                  <a
                    href={currentImage()!.sourceUrl}
                    target="_blank"
                    rel="noopener"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {hostnameOf(currentImage()!.sourceUrl)}
                  </a>
                )}
              </div>
            )}
            {imageMedia().length > 1 && (
              <div class="media-lightbox-counter">
                {lightboxIndex() + 1} / {imageMedia().length}
              </div>
            )}
          </div>
        </div>
      </Show>
    </>
  )
}
