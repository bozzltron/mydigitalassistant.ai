import { createSignal, createMemo, onMount, onCleanup, For, Show } from 'solid-js'
import BrainGraph from './BrainGraph'
import FrameDetail from './FrameDetail'
import type { Frame, Association, Slot, Conflict } from '../../types'

interface TopicMatch {
  frame: Frame
  slots: Slot[]
  similarity: number | null
  associations: Association[]
  episodes: Array<{ role: string; content: string; timestamp: string }>
  conflicts: Conflict[]
}

interface TopicSearchResponse {
  query: string
  semantic_search: boolean
  backend_rev: number
  matches: TopicMatch[]
  summary: string
}

const TYPE_COLORS: Record<string, string> = {
  person: '#f78166',
  concept: '#d2a8ff',
  event: '#79c0ff',
  household: '#7ee787',
  entity: '#ffa657',
}

export default function BrainPage() {
  const [frames, setFrames] = createSignal<Frame[]>([])
  const [associations, setAssociations] = createSignal<Association[]>([])
  const [conflicts, setConflicts] = createSignal<Conflict[]>([])
  const [selectedFrame, setSelectedFrame] = createSignal<Frame | null>(null)
  const [highlightedNodeIds, setHighlightedNodeIds] = createSignal<Set<number>>(new Set())
  const [isLoading, setIsLoading] = createSignal(true)
  const [error, setError] = createSignal<string | null>(null)
  const [searchQuery, setSearchQuery] = createSignal('')
  const [searchResults, setSearchResults] = createSignal<TopicMatch[]>([])
  const [isSearching, setIsSearching] = createSignal(false)
  const [searchError, setSearchError] = createSignal<string | null>(null)
  const [showSearchPanel, setShowSearchPanel] = createSignal(false)
  const [agentName, setAgentName] = createSignal('Brain Observatory')
  const [width, setWidth] = createSignal(800)
  const [height, setHeight] = createSignal(600)
  const [conflictsByFrame, setConflictsByFrame] = createSignal<Record<number, any[]>>({})
  // 3D view state
  const [mode, setMode] = createSignal<'2d' | '3d'>('3d')
  const [touring, setTouring] = createSignal(false)

  const updateDimensions = () => {
    const container = document.getElementById('brain-graph-container')
    if (container) {
      setWidth(container.clientWidth || 800)
      setHeight(container.clientHeight || 600)
    }
  }

  onMount(() => {
    updateDimensions()
    window.addEventListener('resize', updateDimensions)
    // Load saved view mode
    const savedMode = localStorage.getItem('brain-view') as '2d' | '3d' | null
    if (savedMode) setMode(savedMode)
  })

  onCleanup(() => {
    window.removeEventListener('resize', updateDimensions)
  })

  const loadBrainData = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [framesRes, assocRes, conflictsRes] = await Promise.all([
        fetch('/memory/frames'),
        fetch('/memory/associations'),
        fetch('/memory/conflicts'),
      ])

      const [framesData, associationsData, conflictsData] = await Promise.all([
        framesRes.json(),
        assocRes.json(),
        conflictsRes.json(),
      ])

      setFrames(framesData)
      setAssociations(associationsData)
      const pendingConflicts = conflictsData.filter(c => c.status === 'pending')
      setConflicts(pendingConflicts)
      const conflictsByFrameMap: Record<number, any[]> = {}
      for (const c of pendingConflicts) {
        (conflictsByFrameMap[c.frame_id] ||= []).push(c)
      }
      setConflictsByFrame(conflictsByFrameMap)
    } catch (err) {
      console.error('Failed to load brain data:', err)
      setError('Failed to load brain data')
    } finally {
      setIsLoading(false)
    }
  }

  const loadAgentName = async () => {
    try {
      const res = await fetch('/assistant/name')
      const data = await res.json()
      setAgentName(data.name || 'Brain Observatory')
    } catch {
      // ignore
    }
  }

  onMount(() => {
    loadBrainData()
    loadAgentName()
  })

  const handleNodeClick = (node: Node) => {
    const frame = frames().find(f => f.id === node.id)
    if (frame) {
      setSelectedFrame(frame)
      const newHighlighted = new Set(highlightedNodeIds())
      newHighlighted.add(frame.id)
      setHighlightedNodeIds(newHighlighted)
    }
  }

  const handleSearch = async (event: Event) => {
    event.preventDefault()
    if (!searchQuery().trim()) return

    setIsSearching(true)
    setSearchError(null)

    try {
      const params = new URLSearchParams({ q: searchQuery() })
      const res = await fetch(`/memory/search?${params.toString()}`)
      if (!res.ok) throw new Error('Search failed')
      const data: TopicSearchResponse = await res.json()
      setSearchResults(data.matches)
      setShowSearchPanel(true)
    } catch (err) {
      console.error('Search error:', err)
      setSearchError('Search failed. Please try again.')
    } finally {
      setIsSearching(false)
    }
  }

  const handleResultClick = (match: TopicMatch) => {
    setSelectedFrame(match.frame)
    const newHighlighted = new Set(highlightedNodeIds())
    newHighlighted.add(match.frame.id)
    setHighlightedNodeIds(newHighlighted)
    setShowSearchPanel(false)
  }

  const handleBack = () => {
    setSelectedFrame(null)
    setHighlightedNodeIds(new Set())
  }

  const handleModeChange = (newMode: '2d' | '3d') => {
    setMode(newMode)
    localStorage.setItem('brain-view', newMode)
    if (newMode !== '3d') {
      setTouring(false)
    }
  }

  const toggleTour = () => {
    setTouring(t => !t)
  }

  interface Node {
    id: number
    name: string
    type: string
    confidence: number
    priority: number
    essential: boolean
    hasConflict: boolean
  }

  const nodes = createMemo<Node[]>(() => {
    const conflictKeys = new Set(
      conflicts().map(c => `${c.frame_id}-${c.slot_key}`)
    )
    return frames().map(f => ({
      ...f,
      type: f.type || 'entity',
      hasConflict: f.slots?.some((s: Slot) => conflictKeys.has(`${f.id}-${s.key}`)) || false
    }))
  })

  const links = createMemo(() => {
    const nodeMap = new Map(nodes().map(n => [n.id, n]))
    return associations()
      .filter(a => a.from_frame_id && a.to_frame_id)
      .map(a => ({
        ...a,
        source: a.from_frame_id,
        target: a.to_frame_id,
        relationType: a.relation_type,
        confidence: a.confidence,
      }))
      .filter(l => nodeMap.has(l.source) && nodeMap.has(l.target))
  })

  const stats = createMemo(() => {
    const typeCounts: Record<string, number> = {}
    nodes().forEach(n => { typeCounts[n.type] = (typeCounts[n.type] || 0) + 1 })
    return {
      nodeCount: nodes().length,
      linkCount: links().length,
      types: typeCounts,
    }
  })

  return (
    <div class="brain-page">
      <header class="brain-header">
        <h1 id="agent-name">{agentName()}</h1>
        <div class="header-controls">
          <div class="legend">
            <div class="legend-item"><div class="legend-dot" style={{"background":"var(--person)"}} />person</div>
            <div class="legend-item"><div class="legend-dot" style={{"background":"var(--concept)"}} />concept</div>
            <div class="legend-item"><div class="legend-dot" style={{"background":"var(--event)"}} />event</div>
            <div class="legend-item"><div class="legend-dot" style={{"background":"var(--household)"}} />household</div>
            <div class="legend-item"><div class="legend-dot" style={{"background":"var(--entity)"}} />entity</div>
          </div>
          <div class="status" id="status">{isLoading() ? 'Loading...' : 'Live'}</div>
          <div class="topic-search">
            <form onSubmit={handleSearch}>
              <input
                type="text"
                value={searchQuery()}
                onInput={(e) => setSearchQuery(e.target.value)}
                placeholder="Search memory by topic..."
                autocomplete="off"
                class="search-input"
              />
              <button type="submit" class="refresh-btn" disabled={isSearching()}>
                {isSearching() ? 'Searching...' : 'Search'}
              </button>
            </form>
          </div>
          <button class="refresh-btn" onClick={loadBrainData} disabled={isLoading()}>
            {isLoading() ? 'Loading...' : 'Refresh'}
          </button>
          <button
            class="refresh-btn"
            title="Toggle 2D / 3D view"
            onClick={() => handleModeChange(mode() === '3d' ? '2d' : '3d')}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}
          >
            {mode() === '3d' ? '2D' : '3D'}
          </button>
          <button
            class="refresh-btn"
            id="tour-btn"
            title="Auto-orbit tour"
            onClick={toggleTour}
            style={{ display: mode() === '3d' ? 'inline-flex' : 'none', alignItems: 'center', gap: '4px' }}
          >
            {touring() ? 'Stop Tour' : 'Tour'}
          </button>
        </div>
      </header>

      <Show when={error()}>
        <div class="error-banner">{error()}</div>
      </Show>

      <div class="brain-content">
        <Show when={showSearchPanel()}>
          <div class="brain-sidebar search-panel-overlay">
            <div class="search-panel">
              <div class="topic-panel-header">
                <div class="topic-panel-title">Topic memory: {searchQuery()}</div>
                <button class="topic-panel-close" onClick={() => setShowSearchPanel(false)}>
                  ×
                </button>
              </div>
              <Show when={searchError()}>
                <div class="search-error">{searchError()}</div>
              </Show>
              <div class="topic-results">
                <For each={searchResults()}>
                  {(match) => (
                    <div class="topic-card" onClick={() => handleResultClick(match)}>
                      <div class="topic-card-head">
                        <div class="topic-card-dot" style={{ background: TYPE_COLORS[match.frame.type] || TYPE_COLORS.entity }} />
                        <div class="topic-card-name">{match.frame.name}</div>
                        {match.similarity != null && (
                          <div class="topic-card-sim">{Math.round(match.similarity * 100)}% match</div>
                        )}
                      </div>
                      <div class="topic-meta">
                        {match.frame.type} · confidence {Math.round((match.frame.confidence || 0.5) * 100)}%
                        · priority {Math.round((match.frame.priority || 0.5) * 100)}%
                        {match.frame.essential ? ' · essential' : ''}
                        {match.similarity == null ? ' · keyword match' : ''}
                      </div>
                      <div class="conf-bar">
                        <div style={{ width: `${Math.round((match.frame.confidence || 0.5) * 100)}%` }} />
                      </div>
                    </div>
                  )}
                </For>
                <Show when={!isSearching() && searchResults().length === 0 && searchQuery()}>
                  <div class="topic-empty">No results found for &ldquo;{searchQuery()}&rdquo;</div>
                </Show>
              </div>
            </div>
          </div>
        </Show>

        <div class="brain-main">
          {selectedFrame() ? (
            <FrameDetail frame={selectedFrame()} onBack={handleBack} conflicts={conflicts()} />
          ) : (
            <div class="brain-graph-container" id="brain-graph-container">
              <Show when={!isLoading() && nodes().length === 0}>
                <div class="empty-state">
                  <h2>No memories yet</h2>
                  <p>Start chatting to build your brain</p>
                </div>
              </Show>
              <Show when={!isLoading() && nodes().length > 0}>
                <BrainGraph
                  nodes={nodes}
                  links={links}
                  width={width}
                  height={height}
                  onNodeClick={handleNodeClick}
                  highlightedNodeIds={highlightedNodeIds}
                  conflictsByFrame={conflictsByFrame}
                  mode={mode}
                  onModeChange={handleModeChange}
                  touring={touring}
                  setTouring={setTouring}
                />
              </Show>
              <Show when={isLoading()}>
                <div class="graph-placeholder">Loading brain...</div>
              </Show>
              <div class="stats-bar">
                <span><span class="stat-value">{stats().nodeCount}</span> nodes</span>
                <span><span class="stat-value">{stats().linkCount}</span> edges</span>
                {Object.entries(stats().types).length > 0 && (
                  <span>{Object.entries(stats().types).map(([t, c]) => `${c} ${t}`).join(' · ')}</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}