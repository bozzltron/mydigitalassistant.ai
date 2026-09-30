import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js'
import type { MediaContent } from '../../types/chat'
import { hostnameOf } from '../../utils/media'

interface MediaGridProps {
  media: MediaContent[]
  onOpenLightbox?: (media: MediaContent, index: number, allMedia: MediaContent[]) => void
}

export default function MediaGrid(props: MediaGridProps) {
  const [lightboxOpen, setLightboxOpen] = createSignal(false)
  const [lightboxIndex, setLightboxIndex] = createSignal(0)
  const [loadedImages, setLoadedImages] = createSignal<Set<number>>(new Set())

  // props.media, not a destructured snapshot: the memo below would otherwise be
  // built once from a value that can never change, so imageMedia() would never
  // invalidate and the grid would not follow a new `media` prop.
  const imageMedia = createMemo(() =>
    props.media.filter(m => m.type === 'image')
  )

  // The media prop can shrink between renders (a re-extraction, a reconciled
  // stream). Close the lightbox, or clamp the index, so the accessor below can
  // never index past the end of the list.
  createEffect(() => {
    const count = imageMedia().length
    if (count === 0) {
      if (lightboxOpen()) closeLightbox()
      return
    }
    setLightboxIndex(i => Math.min(i, count - 1))
  })

  const currentImage = createMemo(() => imageMedia()[lightboxIndex()])

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
    setLightboxIndex(i => (i - 1 + imageMedia().length) % imageMedia().length)
  }

  const goToNext = () => {
    setLightboxIndex(i => (i + 1) % imageMedia().length)
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

  const handleImageLoad = (index: number) => {
    setLoadedImages(prev => {
      const next = new Set(prev)
      next.add(index)
      return next
    })
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
        <For each={imageMedia()}>
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
              <a
                href={item.sourceUrl || item.url}
                target="_blank"
                rel="noopener"
                onClick={(e) => e.stopPropagation()}
              >
                <img
                  src={item.url}
                  alt={item.title || ''}
                  loading="lazy"
                  onLoad={() => handleImageLoad(index())}
                  classList={{ 'is-loaded': loadedImages().has(index()) }}
                />
                <div class="grid-item-overlay">
                  {item.title && <div class="grid-item-title">{item.title}</div>}
                  {item.sourceUrl && (
                    <div class="grid-item-source">
                      {hostnameOf(item.sourceUrl)}
                    </div>
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
              </a>
            </div>
          )}
        </For>
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
              src={currentImage()!.url}
              alt={currentImage()!.title || ''}
            />
            {(currentImage()!.title || currentImage()!.sourceUrl) && (
              <div class="media-lightbox-caption">
                {currentImage()!.title && (
                  <div class="media-caption-title">
                    {currentImage()!.title}
                  </div>
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
