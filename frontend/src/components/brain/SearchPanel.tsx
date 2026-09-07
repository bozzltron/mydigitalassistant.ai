import { createSignal } from 'solid-js'
import { getSearchResults } from '../../services/api'

interface SearchResult {
  id: number
  url: string
  title: string
  content: string
  relevance: number
}

interface SearchPanelProps {
  onResultClick?: (resultId: number) => void
}

export default function SearchPanel(props: SearchPanelProps) {
  const [searchQuery, setSearchQuery] = createSignal('')
  const [results, setResults] = createSignal<SearchResult[]>([])
  const [isLoading, setIsLoading] = createSignal(false)
  const [error, setError] = createSignal<string | null>(null)

  const handleSearch = async (e: Event) => {
    e.preventDefault()
    
    if (!searchQuery().trim()) return
    
    setIsLoading(true)
    setError(null)
    
    try {
      // Would call real API
      console.log('Searching for:', searchQuery())
      
      // Mock results
      const mockResults: SearchResult[] = [
        {
          id: 1,
          url: 'https://example.com/article1',
          title: 'Travel Planning Guide',
          content: 'How to plan a successful trip...',
          relevance: 0.95
        },
        {
          id: 2,
          url: 'https://example.com/article2', 
          title: 'Budget Travel Tips',
          content: 'Save money while traveling...',
          relevance: 0.87
        },
        {
          id: 3,
          url: 'https://example.com/article3',
          title: 'Flight Booking Services',
          content: 'Compare flight prices from different carriers...',
          relevance: 0.72
        }
      ]
      
      setResults(mockResults)
    } catch (err) {
      setError('Search failed. Please try again.')
      console.error('Search error:', err)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div class="search-panel">
      <h3>Memory Search</h3>
      
      <form onSubmit={handleSearch} class="search-form">
        <input
          type="text"
          value={searchQuery()}
          onInput={(e) => setSearchQuery(e.target.value)}
          placeholder="Search your knowledge..."
          class="search-input"
        />
        
        <button type="submit" disabled={isLoading()}>
          {isLoading() ? 'Searching...' : 'Search'}
        </button>
      </form>
      
      <Show when={error()}>
        <div class="search-error">{error()}</div>
      </Show>
      
      <Show when={!isLoading() && results().length > 0}>
        <div class="search-results">
          <h4>Search Results ({results().length})</h4>
          
          <div class="results-list">
            {results().map(result => (
              <div 
                key={result.id} 
                class="search-result"
                onClick={() => props.onResultClick?.(result.id)}
              >
                <a href={result.url} target="_blank" rel="noopener noreferrer">
                  <h5>{result.title}</h5>
                </a>
                
                <div class="result-content">
                  {result.content}
                </div>
                
                <div class="result-meta">
                  <span class="relevance">Relevance: {(result.relevance * 100).toFixed(0)}%</span>
                  <a href={result.url} target="_blank" rel="noopener noreferrer">
                    {new URL(result.url).hostname}
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>
      </Show>
      
      <Show when={!isLoading() && results().length === 0 && searchQuery()}>
        <div class="no-results">
          No results found for "{searchQuery()}"
        </div>
      </Show>
    </div>
  )
}