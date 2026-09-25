import { createSignal, createEffect, onMount, onCleanup } from 'solid-js'
import Graph from 'graphology'
import Sigma from 'sigma'
import ForceAtlas2 from 'graphology-layout-forceatlas2'
import type { BrainConflict, GraphLink, GraphNode } from '../../types'
import { typeColor, hexToRgba, nodeRadius, relationColor, tooltipHtml } from './brainLib'

interface BrainGraphSigmaProps {
  nodes: () => GraphNode[]
  links: () => GraphLink[]
  width: () => number
  height: () => number
  onNodeClick?: (node: GraphNode) => void
  highlightedNodeIds: () => Set<number>
  conflictsByFrame: () => Record<number, BrainConflict[]>
  touring: () => boolean
  setTouring: (touring: boolean) => void
  onModeChange: (mode: '2d' | '3d') => void
  onTooltip?: (html: string, x: number, y: number) => void
  onHideTooltip?: () => void
}

/**
 * Node attributes stored in the graphology graph. `frameType` holds the
 * semantic category (person/concept/…) because sigma v2 uses the `type`
 * attribute to select its WebGL render program ("circle", "line", …) — a
 * category value there makes sigma throw. `type` is deliberately left unset
 * so sigma applies its default render program.
 *
 * `withPosition` is disabled when merging attributes into an existing node so
 * its ForceAtlas2-computed layout position is preserved.
 */
function nodeAttributes(n: GraphNode, withPosition = true) {
  return {
    ...(withPosition
      ? {
          x: n.x ?? Math.random() * 800 - 400,
          y: n.y ?? Math.random() * 600 - 300,
        }
      : {}),
    size: nodeRadius(n),
    color: typeColor(n.type),
    label: n.name,
    name: n.name,
    frameType: n.type,
    confidence: n.confidence,
    priority: n.priority,
    essential: n.essential,
    hasConflict: n.hasConflict,
    slots: n.slots,
  }
}

function edgeAttributes(l: GraphLink) {
  return {
    relationType: l.relationType,
    confidence: l.confidence,
    color: relationColor(l.relationType),
    size: Math.max(0.5, 2 * (l.confidence || 0.5)),
  }
}

/** Deduplicate associations by unordered pair; graphology is not a multi-graph. */
function uniquePairs(links: GraphLink[]): [GraphLink, string][] {
  const seen = new Set<string>()
  const out: [GraphLink, string][] = []
  for (const l of links) {
    if (l.source === l.target) continue
    const pair = l.source < l.target ? `${l.source}-${l.target}` : `${l.target}-${l.source}`
    if (seen.has(pair)) continue
    seen.add(pair)
    out.push([l, pair])
  }
  return out
}

function buildGraph(nodes: GraphNode[], links: GraphLink[]): Graph {
  const g = new Graph()

  nodes.forEach(n => {
    g.addNode(String(n.id), nodeAttributes(n))
  })

  uniquePairs(links).forEach(([l]) => {
    if (g.hasNode(String(l.source)) && g.hasNode(String(l.target))) {
      g.addEdge(String(l.source), String(l.target), edgeAttributes(l))
    }
  })

  return g
}

export default function BrainGraphSigma(props: BrainGraphSigmaProps) {
  let containerEl: HTMLDivElement | null = null
  const [sigmaInstance, setSigmaInstance] = createSignal<Sigma | null>(null)
  const [graph, setGraph] = createSignal<Graph | null>(null)
  const [neighborsMap, setNeighborsMap] = createSignal<Map<number, Set<number>>>(new Map())
  const [highlightedFrameIds, setHighlightedFrameIds] = createSignal<Set<number>>(new Set())
  const [isInitialized, setIsInitialized] = createSignal(false)

  function setContainerRef(el: HTMLDivElement | null) {
    containerEl = el
  }

  function updateNeighborsMap(links: GraphLink[]) {
    const newNeighborsMap = new Map<number, Set<number>>()
    links.forEach(l => {
      if (!newNeighborsMap.has(l.source)) newNeighborsMap.set(l.source, new Set<number>())
      if (!newNeighborsMap.has(l.target)) newNeighborsMap.set(l.target, new Set<number>())
      newNeighborsMap.get(l.source)!.add(l.target)
      newNeighborsMap.get(l.target)!.add(l.source)
    })
    setNeighborsMap(newNeighborsMap)
  }

  /** Hover (highlightedFrameIds) merged with the persistent click selection. */
  function computeHighlightedNodes(): Set<number> {
    const lit = new Set<number>()
    const seed = new Set<number>([...highlightedFrameIds(), ...props.highlightedNodeIds()])
    for (const id of seed) {
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
        color: typeColor(data.frameType as string),
      }))
      sigma.setSetting('edgeReducer', (edge, data) => ({
        ...data,
        color: data.color,
      }))
      sigma.refresh()
      return
    }

    sigma.setSetting('nodeReducer', (node, data) => {
      const isLit = highlighted.has(Number(node))
      const baseColor = typeColor(data.frameType as string)
      return {
        ...data,
        color: isLit ? baseColor : hexToRgba(baseColor, 0.18),
      }
    })

    sigma.setSetting('edgeReducer', (edge, data) => {
      const [source, target] = graph()?.extremities(edge) ?? []
      const isLit = highlighted.has(Number(source)) || highlighted.has(Number(target))
      return {
        ...data,
        color: isLit ? data.color : hexToRgba(data.color as string, 0.12),
      }
    })

    sigma.refresh()
  }

  async function showTooltip(event: MouseEvent, d: GraphNode) {
    let slots = d.slots
    if (!slots) {
      try {
        const res = await fetch(`/memory/frames/${d.id}/slots`)
        slots = await res.json() as GraphNode['slots']
        d.slots = slots
      } catch {
        slots = []
      }
    }
    const rect = document.getElementById('brain-graph-container')?.getBoundingClientRect()
    if (!rect) return
    let tx = event.clientX - rect.left + 15
    const ty = event.clientY - rect.top - 10
    if (tx + 280 > rect.width) tx = event.clientX - rect.left - 295
    props.onTooltip?.(tooltipHtml({ ...d, slots }, props.conflictsByFrame()), tx, ty)
  }

  async function initSigma() {
    if (isInitialized() || !containerEl || !containerEl.isConnected || containerEl.offsetWidth === 0) {
      return
    }

    try {
      const g = buildGraph(props.nodes(), props.links())
      setGraph(g)
      updateNeighborsMap(props.links())

      // Run ForceAtlas2 layout so every node has a valid (x, y) before Sigma
      // consumes it (Sigma throws on nodes without positions).
      ForceAtlas2.assign(g, {
        iterations: 100,
        settings: {
          gravity: 1,
          scalingRatio: 10,
          slowDown: 1,
        },
      })

      const sigma = new Sigma(g, containerEl, {
        renderLabels: true,
        labelFont: 'Inter, system-ui, sans-serif',
        labelSize: 12,
        labelColor: { attribute: 'color' },
        hideEdgesOnMove: true,
        nodeReducer: (node, data) => ({
          ...data,
          size: (data.size as number) || nodeRadius({ confidence: data.confidence as number }),
          color: typeColor(data.frameType as string),
        }),
        edgeReducer: (edge, data) => ({
          ...data,
          color: data.color,
          size: (data.size as number) || Math.max(0.5, 2 * ((data.confidence as number) || 0.5)),
        }),
      })

      setSigmaInstance(sigma)
      setIsInitialized(true)

      // eslint-disable-next-line solid/reactivity
      sigma.on('clickNode', (payload) => {
        const nodeData = g.getNodeAttributes(payload.node) as unknown as GraphNode
        showTooltip(payload.event.original, nodeData)
        props.onNodeClick?.(nodeData)
      })

      // eslint-disable-next-line solid/reactivity
      sigma.on('enterNode', (payload) => {
        const nodeId = Number(payload.node)
        const highlighted = new Set<number>(highlightedFrameIds())
        highlighted.add(nodeId)
        const nbrs = neighborsMap().get(nodeId)
        if (nbrs) nbrs.forEach(n => highlighted.add(n))
        setHighlightedFrameIds(highlighted)
        applyHighlight(sigma)
      })

      // eslint-disable-next-line solid/reactivity
      sigma.on('leaveNode', () => {
        setHighlightedFrameIds(new Set<number>())
        applyHighlight(sigma)
      })

      sigma.on('clickStage', () => {
        props.onHideTooltip?.()
      })

      const handleResize = () => {
        if (containerEl && sigmaInstance()) {
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

  function refreshGraph() {
    const sigma = sigmaInstance()
    const g = graph()
    if (!sigma || !g) return

    const newNodes = props.nodes()
    const newLinks = props.links()

    // Add / update nodes. Existing nodes keep their layout positions.
    newNodes.forEach(n => {
      const key = String(n.id)
      if (!g.hasNode(key)) {
        g.addNode(key, nodeAttributes(n))
      } else {
        // Keep existing layout position: merge attrs without x/y.
        g.mergeNodeAttributes(key, nodeAttributes(n, false))
      }
    })

    // Add / update edges (deduped by unordered pair).
    uniquePairs(newLinks).forEach(([l]) => {
      const source = String(l.source)
      const target = String(l.target)
      if (!g.hasNode(source) || !g.hasNode(target)) return
      if (g.hasEdge(source, target)) {
        const edge = g.edge(source, target)
        if (edge !== undefined) g.mergeEdgeAttributes(edge, edgeAttributes(l))
      } else {
        g.addEdge(source, target, edgeAttributes(l))
      }
    })

    updateNeighborsMap(newLinks)
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
    props.highlightedNodeIds()
    if (sigmaInstance()) {
      applyHighlight(sigmaInstance()!)
    }
  })

  // Auto-orbit camera tour while touring() is on.
  createEffect(() => {
    if (!props.touring()) return
    const sigma = sigmaInstance()
    if (!sigma) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const s = sigmaInstance()
      if (!s || !props.touring()) return
      const state = s.getCamera().getState()
      s.getCamera().setState({ ...state, angle: state.angle + (now - last) * 0.0004 })
      last = now
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    onCleanup(() => cancelAnimationFrame(raf))
  })

  return (
    <div ref={setContainerRef} id="brain-canvas-sigma" style={{ 'width': '100%', 'height': '100%', 'position': 'absolute', 'inset': '0' }} />
  )
}