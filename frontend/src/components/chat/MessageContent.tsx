import { createMemo } from 'solid-js'
import { marked } from 'marked'
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

interface MessageContentProps {
  message: ChatMessage
  onOpenLightbox?: (media: MediaContent, index: number, allMedia: MediaContent[]) => void
}

export default function MessageContent(props: MessageContentProps) {
  const { message, onOpenLightbox } = props

  const media = createMemo(() => extractAllMedia(message()))
  const heroMedia = createMemo(() => getHeroMedia(media()))
  const gridMedia = createMemo(() => getGridMedia(media(), heroMedia()))
  const previewCards = createMemo(() => getPreviewCards(media()))

  const htmlContent = createMemo(() => marked.parse(message().content || '') as string)

  return (
    <div class="message-content">
      {heroMedia() && (
        <MediaCard
          media={heroMedia()!}
          onOpenLightbox={onOpenLightbox}
        />
      )}

      <div class="msg-markdown" innerHTML={htmlContent()} />

      {gridMedia().length > 0 && (
        <MediaGrid
          media={gridMedia()}
          onOpenLightbox={onOpenLightbox}
        />
      )}

      {previewCards().length > 0 && (
        <PreviewCards cards={previewCards()} />
      )}
    </div>
  )
}