import { createMemo } from 'solid-js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { extractAllMedia, getHeroMedia, getGridMedia, getPreviewCards } from '../../utils/media'
import MediaCard from './MediaCard'
import MediaGrid from './MediaGrid'
import PreviewCards from './PreviewCards'
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

export default function MessageContent(props: {
  message: () => ChatMessage
  onOpenLightbox?: (media: MediaContent, index: number, allMedia: MediaContent[]) => void
}) {
  const media = createMemo(() => extractAllMedia(props.message()))
  const heroMedia = createMemo(() => getHeroMedia(media()))
  const gridMedia = createMemo(() => getGridMedia(media(), heroMedia()))
  const previewCards = createMemo(() => getPreviewCards(media()))

  const htmlContent = createMemo(() => marked.parse(props.message().content || '') as string)

  return (
    <div class="message-content">
      {heroMedia() && (
        <MediaCard
          media={heroMedia()!}
          onOpenLightbox={props.onOpenLightbox}
        />
      )}

      {/* eslint-disable-next-line solid/no-innerhtml -- content sanitized by DOMPurify */}
      <div class="msg-markdown" innerHTML={DOMPurify.sanitize(htmlContent())} />

      {gridMedia().length > 0 && (
        <MediaGrid
          media={gridMedia()}
          onOpenLightbox={props.onOpenLightbox}
        />
      )}

      {previewCards().length > 0 && (
        <PreviewCards cards={previewCards()} />
      )}
    </div>
  )
}