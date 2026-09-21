import { createSignal, createMemo, createEffect, onCleanup, For } from 'solid-js'

interface MediaGridItem {
  type: 'image' | 'preview-card'
  url: string
  thumbnail?: string
  title?: string
  description?: string
  sourceUrl?: string
  aspectRatio?: number
  width?: number
  height?: number
}

interface MediaGridProps {
  media: MediaGridItem[]
  onOpenLightbox?: (media: MediaGridItem, index: number, allMedia: MediaGridItem[]) => void
}

export default function MediaGrid(props: MediaGridProps) {
  const { media, onOpenLightbox } = props
  const [lightboxOpen, setLightboxOpen] = createSignal(false)
  const [lightboxIndex, setLightboxIndex] = createSignal(0)
  const [loadedImages, setLoadedImages] = createSignal<Set<number>>(new Set())

  const imageMedia = createMemo(() =>
    media.filter(m => m.type === 'image')
  )

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

  const handleGridItemClick = (index: number, e: MouseEvent) => {
    if (onOpenLightbox) {
      onOpenLightbox(imageMedia()[index], index, imageMedia())
    } else {
      openLightbox(index)
    }
    e.preventDefault()
  }

  const getAspectRatioClass = (item: MediaGridItem) => {
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
                  handleGridItemClick(index(), e as unknown as MouseEvent)
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
                  style={{ opacity: loadedImages().has(index()) ? 1 : 0 }}
                />
                <div class="grid-item-overlay">
                  {item.title && <div class="grid-item-title">{item.title}</div>}
                  {item.sourceUrl && (
                    <div class="grid-item-source">
                      {new URL(item.sourceUrl).hostname}
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

      {lightboxOpen() && imageMedia().length > 0 && (
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
                disabled={imageMedia().length <= 1}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="15 18 9 12 15 6" />
                </svg>
              </button>
              <button
                class="media-lightbox-nav media-lightbox-nav--next"
                onClick={(e) => { e.stopPropagation(); goToNext(); }}
                aria-label="Next image"
                disabled={imageMedia().length <= 1}
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
              src={imageMedia()[lightboxIndex()].url}
              alt={imageMedia()[lightboxIndex()].title || ''}
            />
            {(imageMedia()[lightboxIndex()].title || imageMedia()[lightboxIndex()].sourceUrl) && (
              <div class="media-lightbox-caption">
                {imageMedia()[lightboxIndex()].title && (
                  <div style={{ fontWeight: 500, marginBottom: '4px' }}>
                    {imageMedia()[lightboxIndex()].title}
                  </div>
                )}
                {imageMedia()[lightboxIndex()].sourceUrl && (
                  <a
                    href={imageMedia()[lightboxIndex()].sourceUrl}
                    target="_blank"
                    rel="noopener"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {new URL(imageMedia()[lightboxIndex()].sourceUrl!).hostname}
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
      )}
    </>
  )
}