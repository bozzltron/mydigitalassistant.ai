import { createMemo, Show } from 'solid-js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { searchMedia, getHeroMedia, getGridMedia, getExtraVideos } from '../../utils/media'
import { collectGroundingSlots, applyGrounding } from '../../utils/grounding'
import MediaCard from './MediaCard'
import MediaGrid from './MediaGrid'
import VideoGallery from './VideoGallery'
import type { ChatMessage, MediaContent } from '../../types/chat'

marked.use({
  renderer: {
    link(token) {
      const href = token.href
      const title = token.title ? ` title="${token.title}"` : ''
      return `<a href="${href}"${title} target="_blank" rel="noopener">${token.text}</a>`
    }
  }
})

// The backend appends a `**Sources:**` footer to search answers. Render it after
// the media so it ends the response, instead of sitting between the answer text
// and the extra images/video.
const SOURCES_MARKER = '\n\n**Sources:**'

export default function MessageContent(props: {
  message: () => ChatMessage
  onOpenLightbox?: (media: MediaContent, index: number, allMedia: MediaContent[]) => void
}) {
  // Media comes only from the message's search results (see `searchMedia`).
  const media = createMemo(() => searchMedia(props.message()))
  const heroMedia = createMemo(() => getHeroMedia(media()))
  const heroIsVideo = createMemo(() => {
    const hero = heroMedia()
    return hero?.type === 'youtube' || hero?.type === 'video'
  })
  const gridMedia = createMemo(() => getGridMedia(media(), heroMedia()))
  // The gallery gets every video: the hero when the hero is a video (so the
  // first is embedded and the rest are thumbnails), otherwise just the extras.
  const videos = createMemo(() => {
    const hero = heroMedia()
    const extras = getExtraVideos(media(), hero)
    return hero && (hero.type === 'youtube' || hero.type === 'video') ? [hero, ...extras] : extras
  })

  const parts = createMemo(() => {
    const content = props.message().content || ''
    const idx = content.indexOf(SOURCES_MARKER)
    if (idx === -1) return { body: content, sources: '' }
    return { body: content.slice(0, idx), sources: content.slice(idx).trim() }
  })

  const bodyHtml = createMemo(() =>
    // Sanitize first, then annotate: `applyGrounding` only ever adds our own
    // spans (with a controlled `data-confidence` tooltip) to the safe markup.
    applyGrounding(
      DOMPurify.sanitize(marked.parse(parts().body) as string),
      collectGroundingSlots(props.message())
    )
  )
  const sourcesHtml = createMemo(() =>
    parts().sources ? (marked.parse(parts().sources) as string) : ''
  )

  return (
    <div class="message-content">
      {/* Dynamic content assembles in one order: the hero block — the video
          gallery (player plus its thumbnails) when the query asked for video,
          else the lead image — then the body, then the image grid, then the
          sources. Each block owns its own top margin. */}
      <Show when={heroMedia()}>
        <Show
          when={heroIsVideo()}
          fallback={<MediaCard media={heroMedia()!} onOpenLightbox={props.onOpenLightbox} />}
        >
          <VideoGallery videos={videos()} />
        </Show>
      </Show>

      {/* eslint-disable-next-line solid/no-innerhtml -- sanitized by DOMPurify, then annotated */}
      <div class="msg-markdown" innerHTML={bodyHtml()} />

      <Show when={gridMedia().length > 0}>
        <MediaGrid media={gridMedia()} onOpenLightbox={props.onOpenLightbox} />
      </Show>

      <Show when={parts().sources}>
        {/* eslint-disable-next-line solid/no-innerhtml -- content sanitized by DOMPurify */}
        <div class="msg-markdown msg-sources-block" innerHTML={DOMPurify.sanitize(sourcesHtml())} />
      </Show>
    </div>
  )
}
