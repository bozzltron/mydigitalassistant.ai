import { createSignal, createEffect, onMount, onCleanup } from 'solid-js'
import Graph from 'graphology'
import Sigma from 'sigma'
import ForceAtlas2 from 'graphology-layout-forceatlas2'

interface Conflict {
  id: number
  frame_id: number
  slot_key: string
  existing_value: string | null
  new_value: string | null
  status: string
}

interface Node {
  id: number
  name: string
  type: string
  confidence: number
  priority: number
  essential: boolean
  x?: number
  y?: number
  z?: number
  vx?: number
  vy?: number
  vz?: number
  fx?: number | null
  fy?: number | null
  fz?: number | null
  hasConflict: boolean
  slots?: Array<{ key: string; value: string; confidence?: number }>
}

interface Link {
  source: number
  target: number
  relationType: string
  confidence: number
}

interface BrainGraphSigmaProps {
  nodes: () => Node[]
  links: () => Link[]
  width: () => number
  height: () => number
  onNodeClick?: (node: Node) => void
  highlightedNodeIds: () => Set<number>
  conflictsByFrame: () => Record<number, Conflict[]>
  touring: () => boolean
  setTouring: (touring: boolean) => void
  onModeChange: (mode: '2d' | '3d') => void
}

const TYPE_COLORS: Record<string, string> = {
  person: '#f78166',
  concept: '#d2a8ff',
  event: '#79c0ff',
  household: '#7ee787',
  entity: '#ffa657',
}

const RELATION_GROUPS = [
  { re: /^(is_a|instance_of|type_of|part_of|has|subclass_of)$/, color: '#79c0ff', label: 'taxonomy / structure' },
  { re: /(located_in|based_in|place|city|country)/, color: '#7ee787', label: 'spatial' },
  { re: /(founded|follows|inquir|member|works_for|created|produced|wrote|compared|participation)/, color: '#f78166', label: 'social / agency' },
  { re: /(source|citation|reference|forecast|compare|lists)/, color: '#d2a8ff', label: 'informational' },
  { re: /^related_to$|^associated/, color: '#4a5470', label: 'related (generic)' },
]
const FALLBACK_EDGE_COLOR = '#8b9bb8'

function relationColor(relationType: string): string {
  for (const g of RELATION_GROUPS) {
    if (g.re.test(relationType)) return g.color
  }
  return FALLBACK_EDGE_COLOR
}

function nodeRadius(d: Node): number {
  const base = 4
  const conf = d.confidence || 0.5
  return base + conf * 8
}

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

function esc(value: string): string {
  return String(value ?? '')
    .replace(/&/g, '&').replace(/</g, '<')
    .replace(/>/g, '>').replace(/"/g, '"')
    .replace(/'/g, '&apos;')
}

function escAttr(value: string): string {
  return String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'")
}

function tooltipHtml(d: Node, conflictsByFrame: Record<number, Conflict[]>): string {
  const conf = Math.round((d.confidence || 0.5) * 100)
  const pri = Math.round((d.priority || 0.5) * 100)

  const slotsHtml = (d.slots || []).slice(0, 5).map(s => {
    const sConf = s.confidence != null ? Math.round(s.confidence * 100) : null
    return `
    <div class="tooltip-slot">
      <span class="tooltip-slot-key">${esc(s.key)}:</span>
      <span class="tooltip-slot-val">${esc(s.value)}</span>
      ${sConf != null ? `<span class="tooltip-slot-conf" title="slot confidence">${sConf}%</span>` : ''}
    </div>
  `}).join('')

  const frameConflicts = (conflictsByFrame[d.id] as Conflict[]) || []
  const conflictsHtml = frameConflicts.map(c => `
    <div class="tooltip-conflict">
      <div class="tooltip-conflict-line">
        <span class="tooltip-conflict-key">${esc(c.slot_key)}</span>:
        <span class="tooltip-conflict-old">${esc(c.existing_value ?? '∅')}</span>
        →
        <span class="tooltip-conflict-new">${esc(c.new_value ?? '∅')}</span>
      </div>
      <div class="tooltip-conflict-actions">
        <button onclick="resolveConflict(${c.id}, '${escAttr(c.existing_value ?? '')}')">Keep ${esc(c.existing_value ?? 'old')}</button>
        <button class="use-new" onclick="resolveConflict(${c.id}, '${escAttr(c.new_value ?? '')}')">Use ${esc(c.new_value ?? 'new')}</button>
      </div>
    </div>
  `).join('')

  return `
    <div class="tooltip-name" style="color:${TYPE_COLORS[d.type] || TYPE_COLORS.entity}">${esc(d.name)}</div>
    <div class="tooltip-type">${d.type}</div>
    <div class="tooltip-meta">
      confidence: ${conf}% &nbsp;|&nbsp; priority: ${pri}%
      ${d.essential ? '&nbsp;|&nbsp; <span style="color:var(--warning)">essential</span>' : ''}
      ${d.hasConflict ? '&nbsp;|&nbsp; <span style="color:var(--error)">conflict</span>' : ''}
    </div>
    ${d.slots?.length ? `<div class="tooltip-slots">${slotsHtml}${d.slots.length > 5 ? `<div style="color:var(--text-dim)">+${d.slots.length - 5} more</div>` : ''}</div>` : ''}
    ${frameConflicts.length ? `<div class="tooltip-conflicts"><div class="tooltip-conflicts-title">Pending conflicts</div>${conflictsHtml}</div>` : ''}
  `
}

export default function BrainGraphSigma(props: BrainGraphSigmaProps) {
  let containerEl: HTMLDivElement | null = null
  const [sigmaInstance, setSigmaInstance] = createSignal<Sigma | null>(null)
  const [graph, setGraph] = createSignal<Graph | null>(null)
  const [neighborsMap] = createSignal<Map<number, Set<number>>>(new Map())
  const [highlightedFrameIds] = createSignal<Set<number>>(new Set())
  const [isInitialized, setIsInitialized] = createSignal(false)

  function setContainerRef(el: HTMLDivElement | null) {
    containerEl = el
  }

  function buildGraph(nodes: Node[], links: Link[]): Graph {
    const g = new Graph()
    
    nodes.forEach(n => {
      g.addNode(n.id, {
        ...n,
        x: n.x ?? Math.random() * 800 - 400,
        y: n.y ?? Math.random() * 600 - 300,
        size: nodeRadius(n),
        color: TYPE_COLORS[n.type] || TYPE_COLORS.entity,
        label: n.name,
      })
    })
    
    links.forEach(l => {
      if (g.hasNode(l.source) && g.hasNode(l.target)) {
        g.addEdge(l.source, l.target, {
          relationType: l.relationType,
          confidence: l.confidence,
          color: relationColor(l.relationType),
          size: Math.max(0.5, 2 * (l.confidence || 0.5)),
        })
      }
    })
    
    return g
  }

  function updateNeighborsMap(g: Graph, links: Link[]) {
    const newNeighborsMap = new Map<number, Set<number>>()
    links.forEach(l => {
      if (!newNeighborsMap.has(l.source)) newNeighborsMap.set(l.source, new Set())
      if (!newNeighborsMap.has(l.target)) newNeighborsMap.set(l.target, new Set())
      newNeighborsMap.get(l.source)!.add(l.target)
      newNeighborsMap.get(l.target)!.add(l.source)
    })
    setNeighborsMap(newNeighborsMap)
  }

  function computeHighlightedNodes(): Set<number> {
    const lit = new Set<number>()
    const ids = highlightedFrameIds()
    if (ids.size === 0) return lit
    for (const id of ids) {
      lit.add(id)
      const nbrs = neighborsMap().get(id)
      if (nbrs) nbrs.forEach(n => lit.add(n))
    }
    return lit
  }

  function applyHighlight(sigma: Sigma) {
    const highlighted = computeHighlightedNodes()
    
    if (highlighted.size === 0) {
      sigma.setSetting('nodeReducer', (node, data) => ({
        ...data,
        color: TYPE_COLORS[data.type] || TYPE_COLORS.entity,
        label: data.name,
      }))
      sigma.setSetting('edgeReducer', (edge, data) => ({
        ...data,
        color: data.color,
      }))
      sigma.refresh()
      return
    }

    sigma.setSetting('nodeReducer', (node, data) => {
      const isLit = highlighted.has(node)
      const baseColor = TYPE_COLORS[data.type] || TYPE_COLORS.entity
      return {
        ...data,
        color: isLit ? baseColor : hexToRgba(baseColor, 0.18),
        label: data.name,
      }
    })
    
    sigma.setSetting('edgeReducer', (edge, data) => {
      const source = edge.source
      const target = edge.target
      const isLit = highlighted.has(source) || highlighted.has(target)
      return {
        ...data,
        color: isLit ? data.color : hexToRgba(data.color, 0.12),
      }
    })
    
    sigma.refresh()
  }

  async function initSigma() {
    if (isInitialized() || !containerEl || !containerEl.isConnected || containerEl.offsetWidth === 0) {
      return
    }

    try {
      const g = buildGraph(props.nodes(), props.links())
      setGraph(g)
      updateNeighborsMap(g, props.links())

      // Run ForceAtlas2 layout
      ForceAtlas2.assign(g, {
        iterations: 100,
        settings: {
          gravity: 1,
          scalingRatio: 10,
          slowDown: 1,
        }
      })

      const sigma = new Sigma(g, containerEl, {
        renderLabels: true,
        labelFont: 'Inter, system-ui, sans-serif',
        labelSize: 12,
        labelColor: 'node',
        labelBackground: 'node',
        labelBackgroundColor: 'rgba(0,0,0,0.7)',
        labelGrid: false,
        hideEdgesOnMove: true,
        minEdgeSize: 0.5,
        maxEdgeSize: 4,
        minNodeSize: 1,
        maxNodeSize: 20,
        nodeReducer: (node, data) => ({
          ...data,
          size: data.size || nodeRadius(data),
          color: data.color,
          label: data.name,
        }),
        edgeReducer: (edge, data) => ({
          ...data,
          color: data.color,
          size: data.size || Math.max(0.5, 2 * data.confidence),
        }),
      })

      setSigmaInstance(sigma)
      setIsInitialized(true)

      // Interaction handlers
      // eslint-disable-next-line solid/reactivity
      sigma.on('clickNode', (e: { node: string; event: MouseEvent }) => {
        const nodeData = g.getNodeAttributes(e.node) as unknown as Node
        showTooltip(e.event, nodeData)
        props.onNodeClick?.(nodeData)
      })

      // eslint-disable-next-line solid/reactivity
      sigma.on('enterNode', (e: { node: string }) => {
        const nodeId = parseInt(e.node, 10)
        const highlighted = new Set(highlightedFrameIds())
        highlighted.add(nodeId)
        const nbrs = neighborsMap().get(nodeId)
        if (nbrs) nbrs.forEach(n => highlighted.add(n))
        setHighlightedFrameIds(highlighted)
        applyHighlight(sigma)
      })

      // eslint-disable-next-line solid/reactivity
      sigma.on('leaveNode', () => {
        setHighlightedFrameIds(new Set())
        applyHighlight(sigma)
      })

      sigma.on('clickStage', () => {
        const tooltip = document.getElementById('tooltip')
        if (tooltip) tooltip.classList.remove('visible')
      })

      // Handle resize
      const handleResize = () => {
        if (containerEl) {
          sigma.resize()
        }
      }
      window.addEventListener('resize', handleResize)

      onCleanup(() => {
        window.removeEventListener('resize', handleResize)
        sigma.kill()
      })

      applyHighlight(sigma)
    } catch (e) {
      console.error('Sigma initialization failed:', e)
      props.onModeChange('2d')
    }
  }

  function showTooltip(event: MouseEvent, d: Node) {
    const tooltip = document.getElementById('tooltip')
    if (!tooltip) return
    if (!d.slots) {
      fetch(`/memory/frames/${d.id}/slots`)
        .then(r => r.json())
        .then(slots => { d.slots = slots })
        .catch(() => { d.slots = [] })
        // eslint-disable-next-line solid/reactivity
        .finally(() => {
          tooltip.innerHTML = tooltipHtml(d, props.conflictsByFrame())
          const rect = document.getElementById('brain-graph-container')?.getBoundingClientRect()
          if (!rect) return
          let tx = event.clientX - rect.left + 15
          const ty = event.clientY - rect.top - 10
          if (tx + 280 > rect.width) tx = event.clientX - rect.left - 295
          tooltip.style.left = tx + 'px'
          tooltip.style.top = ty + 'px'
          tooltip.classList.add('visible')
        })
    } else {
      tooltip.innerHTML = tooltipHtml(d, props.conflictsByFrame())
      const rect = document.getElementById('brain-graph-container')?.getBoundingClientRect()
      if (!rect) return
      let tx = event.clientX - rect.left + 15
      const ty = event.clientY - rect.top - 10
      if (tx + 280 > rect.width) tx = event.clientX - rect.left - 295
      tooltip.style.left = tx + 'px'
      tooltip.style.top = ty + 'px'
      tooltip.classList.add('visible')
    }
  }

  function refreshGraph() {
    const sigma = sigmaInstance()
    const g = graph()
    if (!sigma || !g) return

    const currentNodes = new Set(g.nodes().map(n => Number(n)))
    const newNodes = props.nodes()
    const newLinks = props.links()

    // Add new nodes
    newNodes.forEach(n => {
      if (!currentNodes.has(n.id)) {
        g.addNode(n.id, {
          ...n,
          x: n.x ?? Math.random() * 800 - 400,
          y: n.y ?? Math.random() * 600 - 300,
          size: nodeRadius(n),
          color: TYPE_COLORS[n.type] || TYPE_COLORS.entity,
          label: n.name,
        })
      }
    })

    // Update existing nodes
    newNodes.forEach(n => {
      if (g.hasNode(n.id)) {
        g.mergeNodeAttributes(n.id, {
          name: n.name,
          type: n.type,
          confidence: n.confidence,
          priority: n.priority,
          essential: n.essential,
          hasConflict: n.hasConflict,
          size: nodeRadius(n),
          color: TYPE_COLORS[n.type] || TYPE_COLORS.entity,
          label: n.name,
        })
      }
    })

    // Update edges
    const currentEdges = new Set(g.edges().map(e => `${e.source}-${e.target}`))
    newLinks.forEach(l => {
      const edgeKey = `${l.source}-${l.target}`
      if (!currentEdges.has(edgeKey) && g.hasNode(l.source) && g.hasNode(l.target)) {
        g.addEdge(l.source, l.target, {
          relationType: l.relationType,
          confidence: l.confidence,
          color: relationColor(l.relationType),
          size: Math.max(0.5, 2 * (l.confidence || 0.5)),
        })
      } else if (g.hasEdge(l.source, l.target)) {
        const edgeId = g.edge(l.source, l.target)
        g.mergeEdgeAttributes(edgeId, {
          relationType: l.relationType,
          confidence: l.confidence,
          color: relationColor(l.relationType),
          size: Math.max(0.5, 2 * (l.confidence || 0.5)),
        })
      }
    })

    updateNeighborsMap(g, newLinks)
    sigma.refresh()
    applyHighlight(sigma)
  }

  onMount(() => {
    const checkReady = () => {
      if (containerEl && containerEl.isConnected && containerEl.offsetWidth > 0 && containerEl.offsetHeight > 0) {
        initSigma()
      } else {
        requestAnimationFrame(checkReady)
      }
    }
    checkReady()
  })

  createEffect(() => {
    props.nodes()
    props.links()
    if (isInitialized()) {
      refreshGraph()
    }
  })

  createEffect(() => {
    props.width()
    props.height()
    if (sigmaInstance() && containerEl) {
      sigmaInstance()!.resize()
    }
  })

  createEffect(() => {
    if (sigmaInstance()) {
      applyHighlight(sigmaInstance()!)
    }
  })

  return (
    <div ref={setContainerRef} id="brain-canvas-sigma" style={{ width: '100%', height: '100%', position: 'absolute', inset: 0 }} />
  )
}