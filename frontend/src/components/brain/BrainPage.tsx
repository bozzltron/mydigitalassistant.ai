import { createSignal, createMemo, onMount, onCleanup, For, Show, ErrorBoundary } from 'solid-js'
import BrainGraph from './BrainGraph'
import FrameDetail from './FrameDetail'
import type {
  BrainAssociation,
  BrainConflict,
  BrainFrame,
  BrainTopicMatch,
  BrainTopicSearchResponse,
  GraphLink,
  GraphNode,
} from '../../types'
import { TYPE_COLORS } from './brainLib'
import { getFrames } from '../../services/api'

function initialViewMode(): '2d' | '3d' {
  try {
    const saved = localStorage.getItem('brain-view')
    return saved === '2d' || saved === '3d' ? saved : '3d'
  } catch {
    return '3d'
  }
}

export default function BrainPage() {
  const [frames, setFrames] = createSignal<BrainFrame[]>([])
  const [associations, setAssociations] = createSignal<BrainAssociation[]>([])
  const [conflicts, setConflicts] = createSignal<BrainConflict[]>([])
  const [selectedFrame, setSelectedFrame] = createSignal<BrainFrame | null>(null)
  const [highlightedNodeIds, setHighlightedNodeIds] = createSignal<Set<number>>(new Set())
  const [isLoading, setIsLoading] = createSignal(true)
  const [error, setError] = createSignal<string | null>(null)
  const [searchQuery, setSearchQuery] = createSignal('')
  const [searchResults, setSearchResults] = createSignal<BrainTopicMatch[]>([])
  const [isSearching, setIsSearching] = createSignal(false)
  const [searchError, setSearchError] = createSignal<string | null>(null)
  const [showSearchPanel, setShowSearchPanel] = createSignal(false)
  const [agentName, setAgentName] = createSignal('Brain Observatory')
  const [width, setWidth] = createSignal(800)
  const [height, setHeight] = createSignal(600)
  const [conflictsByFrame, setConflictsByFrame] = createSignal<Record<number, BrainConflict[]>>({})
  // 3D view state (initial mode read synchronously so saved 2D users never
  // flash the Sigma/WebGL view)
  const [mode, setMode] = createSignal<'2d' | '3d'>(initialViewMode())
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
  })

  onCleanup(() => {
    window.removeEventListener('resize', updateDimensions)
  })

  const loadBrainData = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [framesData, assocRes, conflictsRes] = await Promise.all([
        getFrames(1),
        fetch('/memory/associations'),
        fetch('/memory/conflicts'),
      ])

      const [associationsData, conflictsData] = await Promise.all([
        assocRes.json(),
        conflictsRes.json(),
      ]) as [BrainAssociation[], BrainConflict[]]

      setFrames(framesData as unknown as BrainFrame[])
      setAssociations(associationsData)
      const pendingConflicts = conflictsData.filter(c => c.status === 'pending')
      setConflicts(pendingConflicts)
      const conflictsByFrameMap: Record<number, BrainConflict[]> = {}
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

  const handleNodeClick = (node: GraphNode) => {
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
      const data: BrainTopicSearchResponse = await res.json()
      setSearchResults(data.matches)
      setShowSearchPanel(true)
    } catch (err) {
      console.error('Search error:', err)
      setSearchError('Search failed. Please try again.')
    } finally {
      setIsSearching(false)
    }
  }

  const handleResultClick = (match: BrainTopicMatch) => {
    setSelectedFrame(match.frame)
    const newHighlighted = new Set(highlightedNodeIds())
    newHighlighted.add(match.frame.id)
    setHighlightedNodeIds(newHighlighted)
    setShowSearchPanel(false)
  }

  const handleBack = () => {
    setSelectedFrame(null)
    setHighlightedNodeIds(new Set<number>())
  }

  const handleConflictResolve = async (conflictId: number, value: string) => {
    try {
      const params = new URLSearchParams({ value })
      const res = await fetch(`/memory/conflicts/${conflictId}/resolve?${params.toString()}`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error('Resolve failed')
      await loadBrainData()
    } catch (err) {
      console.error('Failed to resolve conflict:', err)
    }
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

  const nodes = createMemo<GraphNode[]>(() => {
    const conflictFrameIds = new Set(conflicts().map(c => c.frame_id))
    return frames().map(f => ({
      id: f.id,
      name: f.name,
      type: f.type || 'entity',
      confidence: f.confidence,
      priority: f.priority,
      essential: f.essential,
      hasConflict: conflictFrameIds.has(f.id),
    }))
  })

  const links = createMemo<GraphLink[]>(() => {
    const nodeMap = new Map(nodes().map(n => [n.id, n]))
    return associations()
      .filter(a => a.from_frame_id && a.to_frame_id)
      .map(a => ({
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
            style={{ 'display': 'inline-flex', 'align-items': 'center', 'gap': '4px' }}
          >
            {mode() === '3d' ? '2D' : '3D'}
          </button>
          <button
            class="refresh-btn"
            id="tour-btn"
            title="Auto-orbit tour"
            onClick={toggleTour}
            style={{ 'display': mode() === '3d' ? 'inline-flex' : 'none', 'align-items': 'center', 'gap': '4px' }}
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
            <FrameDetail frame={selectedFrame() ?? undefined} onBack={handleBack} conflicts={conflicts()} onConflictResolved={loadBrainData} />
          ) : (
            <div class="brain-graph-container" id="brain-graph-container">
              <Show when={!isLoading() && nodes().length === 0}>
                <div class="empty-state">
                  <h2>No memories yet</h2>
                  <p>Start chatting to build your brain</p>
                </div>
              </Show>
              <Show when={!isLoading() && nodes().length > 0}>
                <ErrorBoundary fallback={<div class="graph-placeholder">The memory graph could not be displayed.</div>}>
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
                    onConflictResolve={handleConflictResolve}
                    touring={touring}
                    setTouring={setTouring}
                  />
                </ErrorBoundary>
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