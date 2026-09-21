import { For } from 'solid-js'
import type { MediaContent } from '../../types/chat'

interface PreviewCardsProps {
  cards: MediaContent[]
}

export default function PreviewCards(props: PreviewCardsProps) {
  const { cards } = props

  return (
    <div class="msg-preview-cards" role="list" aria-label="Link previews">
      <For each={cards}>
        {(card) => (
          <a
            class="msg-preview-card"
            href={card.sourceUrl || card.url}
            target="_blank"
            rel="noopener"
            role="listitem"
          >
            {card.thumbnail ? (
              <img class="msg-preview-card-image" src={card.thumbnail} alt="" loading="lazy" />
            ) : (
              <div class="msg-preview-card-placeholder">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M21 12a9 9 0 01-9 9c-2.5 0-4.8-.8-6.5-2.1" />
                  <path d="M3 3v18h18" />
                  <path d="M21 3l-9 9" />
                </svg>
              </div>
            )}
            <div class="msg-preview-card-body">
              {card.sourceUrl && (
                <div class="msg-preview-card-site">
                  {new URL(card.sourceUrl).hostname}
                </div>
              )}
              {card.title && (
                <div class="msg-preview-card-title">{card.title}</div>
              )}
              {card.description && (
                <div class="msg-preview-card-description">{card.description}</div>
              )}
              {card.sourceUrl && (
                <div class="msg-preview-card-url">
                  {new URL(card.sourceUrl).pathname.slice(0, 50)}
                </div>
              )}
            </div>
          </a>
        )}
      </For>
    </div>
  )
}