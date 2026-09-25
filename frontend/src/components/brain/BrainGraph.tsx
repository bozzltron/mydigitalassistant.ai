import { createSignal, createEffect, createMemo, Show, For, lazy, Suspense, onCleanup, onMount, untrack } from 'solid-js'
import DOMPurify from 'dompurify'
import * as d3 from 'd3'
import type { BrainConflict, GraphLink, GraphNode } from '../../types'
import { relationColor, nodeRadius, tooltipHtml, typeColor, RELATION_GROUPS } from './brainLib'

/**
 * d3 forceLink mutates link.source/target from numeric ids into the actual
 * node datum objects, so we type that post-mutation shape explicitly.
 */
interface SimLink extends d3.SimulationLinkDatum<GraphNode> {
  source: GraphNode
  target: GraphNode
  relationType: string
  confidence: number
}

interface GraphSelections {
  link: d3.Selection<SVGPathElement, SimLink, SVGGElement, unknown>
  node: d3.Selection<SVGGElement, GraphNode, SVGGElement, unknown>
  edgeLabel: d3.Selection<SVGTextElement, SimLink, SVGGElement, unknown>
}

interface BrainGraphProps {
  nodes: () => GraphNode[]
  links: () => GraphLink[]
  width: () => number
  height: () => number
  onNodeClick?: (node: GraphNode) => void
  onConflictResolve?: (conflictId: number, value: string) => void
  highlightedNodeIds: () => Set<number>
  conflictsByFrame: () => Record<number, BrainConflict[]>
  mode: () => '2d' | '3d'
  onModeChange: (mode: '2d' | '3d') => void
  touring: () => boolean
  setTouring: (touring: boolean) => void
}

// Lazy-loaded 3D component (Sigma.js WebGL)
const BrainGraph3D = lazy(() => import('./BrainGraphSigma').then(m => ({ default: m.default })))

function edgePath(d: SimLink): string {
  const sx = d.source.x ?? 0
  const sy = d.source.y ?? 0
  const tx = d.target.x ?? 0
  const ty = d.target.y ?? 0
  const dx = tx - sx
  const dy = ty - sy
  const cx = sx + dx / 2 - dy * 0.08
  const cy = sy + dy / 2 + dx * 0.08
  return `M${sx},${sy}Q${cx},${cy}${tx},${ty}`
}

function edgeMidpoint(d: SimLink): { x: number; y: number } {
  const sx = d.source.x ?? 0
  const sy = d.source.y ?? 0
  const tx = d.target.x ?? 0
  const ty = d.target.y ?? 0
  const dx = tx - sx
  const dy = ty - sy
  const cx = sx + dx / 2 - dy * 0.08
  const cy = sy + dy / 2 + dx * 0.08
  return {
    x: (sx + 2 * cx + tx) / 4,
    y: (sy + 2 * cy + ty) / 4 + 3,
  }
}

export default function BrainGraph(props: BrainGraphProps) {
  const [svgRef, setSvgRef] = createSignal<SVGSVGElement | null>(null)
  const [simulation, setSimulation] = createSignal<d3.Simulation<GraphNode, SimLink> | null>(null)
  const [gSelection, setGSelection] = createSignal<d3.Selection<SVGGElement, unknown, null, undefined> | null>(null)
  const [defsSelection, setDefsSelection] = createSignal<d3.Selection<SVGDefsElement, unknown, null, undefined> | null>(null)
  const [neighborsMap, setNeighborsMap] = createSignal<Map<number, Set<number>>>(new Map())
  const [selections, setSelections] = createSignal<GraphSelections | null>(null)
  const [tooltipVisible, setTooltipVisible] = createSignal(false)
  const [tooltipContent, setTooltipContent] = createSignal('')
  const [tooltipPosition, setTooltipPosition] = createSignal({ x: 0, y: 0 })

  const nodesWithPos = createMemo<GraphNode[]>(() =>
    props.nodes().map(n => ({
      ...n,
      x: n.x ?? props.width() / 2 + (Math.random() - 0.5) * 200,
      y: n.y ?? props.height() / 2 + (Math.random() - 0.5) * 200,
      fx: n.fx ?? null,
      fy: n.fy ?? null,
    }))
  )

  function neighborsOf(id: number): Set<number> {
    return neighborsMap().get(id) || new Set<number>()
  }

  function computeHighlightSet(): Set<number> {
    const lit = new Set<number>()
    for (const id of props.highlightedNodeIds()) {
      lit.add(id)
      neighborsOf(id).forEach(n => lit.add(n))
    }
    return lit
  }

  function clearHighlight() {
    const sel = selections()
    if (!sel) return
    sel.link.classed('dimmed', false).classed('lit', false)
    sel.node.classed('dimmed', false).classed('lit', false)
    sel.edgeLabel.classed('visible', false)
  }

  function applyHighlight(litIds: Set<number>) {
    const sel = selections()
    if (!sel) return
    if (litIds.size === 0) {
      clearHighlight()
      return
    }
    sel.link
      .classed('dimmed', l => !litIds.has(l.source.id) && !litIds.has(l.target.id))
      .classed('lit', l => litIds.has(l.source.id) || litIds.has(l.target.id))
    sel.edgeLabel.classed('visible', l => litIds.has(l.source.id) || litIds.has(l.target.id))
    sel.node
      .classed('dimmed', n => !litIds.has(n.id))
      .classed('lit', n => litIds.has(n.id))
  }

  function buildEdgeLegend(defs: d3.Selection<SVGDefsElement, unknown, null, undefined>) {
    const edgeColors = [...new Set(props.links().map(l => relationColor(l.relationType)))]
    for (const color of edgeColors) {
      const markerId = `arrow-${color.slice(1)}`
      if (defs.select(`#${markerId}`).empty()) {
        defs.append('marker')
          .attr('id', markerId)
          .attr('viewBox', '0 -5 10 10')
          .attr('refX', 14)
          .attr('refY', 0)
          .attr('markerWidth', 5)
          .attr('markerHeight', 5)
          .attr('orient', 'auto')
          .append('path')
          .attr('d', 'M0,-5L10,0L0,5')
          .attr('fill', color)
      }
    }
  }

  async function fetchSlots(d: GraphNode): Promise<GraphNode> {
    if (!d.slots) {
      try {
        const res = await fetch(`/memory/frames/${d.id}/slots`)
        d.slots = await res.json() as GraphNode['slots']
      } catch {
        d.slots = []
      }
    }
    return d
  }

  function showTooltip(event: MouseEvent, d: GraphNode) {
    fetchSlots(d).then(dWithSlots => {
      const svg = svgRef()
      if (!svg) return
      setTooltipContent(tooltipHtml(dWithSlots, props.conflictsByFrame()))
      setTooltipVisible(true)
      const rect = svg.getBoundingClientRect()
      let tx = event.clientX - rect.left + 15
      const ty = event.clientY - rect.top - 10
      if (tx + 280 > rect.width) tx = event.clientX - rect.left - 295
      setTooltipPosition({ x: tx, y: ty })
    })
  }

  function hideTooltip() {
    setTooltipVisible(false)
  }

  function showTooltipFrom3D(html: string, x: number, y: number) {
    setTooltipContent(html)
    setTooltipPosition({ x, y })
    setTooltipVisible(true)
  }

  function render2D() {
    const svg = svgRef()
    if (!svg || !svg.isConnected) return

    const container = d3.select(svg)
    const w = props.width()
    const h = props.height()
    container.attr('width', w).attr('height', h)

    let g = gSelection()
    let defs = defsSelection()

    if (!g || !g.node()?.isConnected) {
      g = container.append('g').attr('class', 'graph')
      setGSelection(g)

      defs = container.append('defs')
      setDefsSelection(defs)

      const glow = defs.append('filter').attr('id', 'glow')
      glow.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'coloredBlur')
      const feMerge = glow.append('feMerge')
      feMerge.append('feMergeNode').attr('in', 'coloredBlur')
      feMerge.append('feMergeNode').attr('in', 'SourceGraphic')

      const strongGlow = defs.append('filter').attr('id', 'strongGlow')
      strongGlow.append('feGaussianBlur').attr('stdDeviation', '6').attr('result', 'coloredBlur')
      const feMerge2 = strongGlow.append('feMerge')
      feMerge2.append('feMergeNode').attr('in', 'coloredBlur')
      feMerge2.append('feMergeNode').attr('in', 'SourceGraphic')

      const root = g
      const zoom = d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
          root.attr('transform', event.transform)
        })
      container.call(zoom)
    }

    // Drop stale per-render state; selections are refreshed below. The old
    // simulation is stopped untracked: render2D runs inside a createEffect
    // that also writes `simulation`, and tracking it here would re-trigger
    // the effect forever (read+write of the same signal in one effect).
    g.selectAll('*').remove()
    setSelections(null)
    untrack(() => simulation()?.stop())

    // forceLink mutates these link objects in place: source/target become nodes.
    const linkData = props.links() as unknown as SimLink[]

    const sim = d3.forceSimulation<GraphNode, SimLink>(nodesWithPos())
      .force('link', d3.forceLink<GraphNode, GraphLink>(props.links()).id((d: GraphNode) => d.id).distance(100).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(w / 2, h / 2))
      .force('collision', d3.forceCollide<GraphNode>().radius((d: GraphNode) => nodeRadius(d) + 8))

    setSimulation(sim)
    buildEdgeLegend(defs!)

    const newNeighborsMap = new Map<number, Set<number>>()
    props.links().forEach(l => {
      if (!newNeighborsMap.has(l.source)) newNeighborsMap.set(l.source, new Set<number>())
      if (!newNeighborsMap.has(l.target)) newNeighborsMap.set(l.target, new Set<number>())
      newNeighborsMap.get(l.source)!.add(l.target)
      newNeighborsMap.get(l.target)!.add(l.source)
    })
    setNeighborsMap(newNeighborsMap)

    const link = g.append('g')
      .attr('class', 'links')
      .selectAll<SVGPathElement, SimLink>('path')
      .data(linkData)
      .join('path')
      .attr('class', 'link')
      .attr('stroke', (d: SimLink) => relationColor(d.relationType))
      .attr('stroke-width', (d: SimLink) => {
        const conf = d.confidence || 0.5
        return conf === 0.5 ? 1.25 : Math.max(0.75, 2.25 * conf)
      })
      .attr('marker-end', (d: SimLink) => `url(#arrow-${relationColor(d.relationType).slice(1)})`)

    link.append('title')
      .text((d: SimLink) => `${d.relationType} (${Math.round((d.confidence || 0.5) * 100)}%)`)

    const edgeLabel = g.append('g')
      .attr('class', 'edge-labels')
      .selectAll<SVGTextElement, SimLink>('text')
      .data(linkData)
      .join('text')
      .attr('class', 'edge-label')
      .attr('text-anchor', 'middle')
      .text((d: SimLink) => d.relationType)

    const node = g.append('g')
      .attr('class', 'nodes')
      .selectAll<SVGGElement, GraphNode>('g')
      .data(nodesWithPos())
      .join('g')
      .attr('class', (d: GraphNode) => d.hasConflict ? 'node has-conflict' : 'node')
      .call(d3.drag<SVGGElement, GraphNode>()
        .on('start', (event: d3.D3DragEvent<SVGGElement, GraphNode, GraphNode>, d: GraphNode) => {
          if (!event.active) sim.alphaTarget(0.3).restart()
          d.fx = d.x ?? 0
          d.fy = d.y ?? 0
        })
        .on('drag', (event: d3.D3DragEvent<SVGGElement, GraphNode, GraphNode>, d: GraphNode) => {
          d.fx = event.x
          d.fy = event.y
        })
        .on('end', (event: d3.D3DragEvent<SVGGElement, GraphNode, GraphNode>, d: GraphNode) => {
          if (!event.active) sim.alphaTarget(0)
          d.fx = null
          d.fy = null
        }))

    node.append('circle')
      .attr('r', (d: GraphNode) => nodeRadius(d))
      .attr('fill', (d: GraphNode) => typeColor(d.type))
      .attr('filter', (d: GraphNode) => d.hasConflict ? 'url(#strongGlow)' : 'url(#glow)')
      .attr('class', (d: GraphNode) => d.hasConflict ? 'conflict-pulse' : '')

    setSelections({ link, node, edgeLabel })

    sim.on('tick', () => {
      link.attr('d', edgePath)
      edgeLabel
        .attr('x', (d: SimLink) => edgeMidpoint(d).x)
        .attr('y', (d: SimLink) => edgeMidpoint(d).y)
      node.attr('transform', (d: GraphNode) => `translate(${d.x},${d.y})`)
    })

    node.on('mouseover', (event: MouseEvent, d: GraphNode) => {
      const lit = new Set<number>([d.id, ...neighborsOf(d.id)])
      applyHighlight(lit)
      showTooltip(event, d)
    })
      .on('mouseout', () => {
        clearHighlight()
        applyHighlight(computeHighlightSet())
        hideTooltip()
      })
      .on('click', (event: MouseEvent, d: GraphNode) => {
        showTooltip(event, d)
        props.onNodeClick?.(d)
      })

    applyHighlight(computeHighlightSet())
  }

  // Render the 2D graph whenever we are in 2D mode and its inputs change.
  createEffect(() => {
    const mode = props.mode()
    props.width()
    props.height()
    props.nodes()
    props.links()
    if (mode !== '2d') return
    if (!svgRef()?.isConnected) return
    render2D()
  })

  // Keep dimmed/lit states in sync with the selected frame (2D).
  createEffect(() => {
    props.highlightedNodeIds()
    selections()
    if (props.mode() === '2d') {
      applyHighlight(computeHighlightSet())
    }
  })

  // Stop the force simulation while in 3D (avoids burning CPU on a hidden view).
  createEffect(() => {
    if (props.mode() === '3d') simulation()?.stop()
  })

  // When 2D unmounts, discard the d3 state so the next 2D session re-initializes.
  createEffect(() => {
    if (props.mode() === '3d') {
      setGSelection(null)
      setDefsSelection(null)
      setSelections(null)
      setNeighborsMap(new Map())
    }
  })

  // Delegated click handler for tooltip conflict-resolution buttons. The
  // tooltip HTML is rendered via innerHTML, so it cannot use Solid handlers.
  function handleTooltipAction(event: MouseEvent) {
    const found = (event.target as HTMLElement)?.closest?.('[data-conflict-resolve]') as HTMLElement | null
    if (!found) return
    const conflictId = Number(found.getAttribute('data-conflict-id'))
    const value = found.getAttribute('data-conflict-value') ?? ''
    if (!Number.isFinite(conflictId)) return
    props.onConflictResolve?.(conflictId, value)
  }
  onMount(() => document.addEventListener('click', handleTooltipAction))
  onCleanup(() => document.removeEventListener('click', handleTooltipAction))

  const is2d = props.mode() === '2d'
  const is3d = props.mode() === '3d'

  return (
    <div class="brain-graph-wrapper" style={{ 'position': 'relative', 'width': '100%', 'height': '100%', 'flex': '1' }}>
      <Show when={is2d}>
        <svg ref={setSvgRef} width={props.width()} height={props.height()} class="brain-canvas">
          <rect width="100%" height="100%" fill="var(--bg)" />
          <g class="stars">
            <For each={Array.from({ length: 200 }, (_, i) => i)}>
              {(_) => (
                <circle
                  cx={Math.random() * props.width()}
                  cy={Math.random() * props.height()}
                  r={Math.random() * 0.8 + 0.2}
                  fill="white"
                  opacity={Math.random() * 0.4 + 0.1}
                />
              )}
            </For>
          </g>
        </svg>
      </Show>

      <Show when={is3d} fallback={<div class="graph-placeholder">Loading 3D view...</div>}>
        <Suspense fallback={<div class="graph-placeholder">Loading 3D view...</div>}>
          <BrainGraph3D
            nodes={props.nodes}
            links={props.links}
            width={props.width}
            height={props.height}
            onNodeClick={props.onNodeClick}
            highlightedNodeIds={props.highlightedNodeIds}
            conflictsByFrame={props.conflictsByFrame}
            touring={props.touring}
            setTouring={props.setTouring}
            onModeChange={props.onModeChange}
            onTooltip={showTooltipFrom3D}
            onHideTooltip={hideTooltip}
          />
        </Suspense>
      </Show>

      <div class="edge-legend" style={{ 'position': 'absolute', 'left': '1rem', 'bottom': '3.2rem', 'z-index': 10 }}>
        <For each={RELATION_GROUPS}>
          {(g) => (
            <div class="legend-item" style={{ 'display': 'flex', 'align-items': 'center', 'gap': '0.45rem', 'font-size': '0.72rem', 'color': 'var(--text-dim)' }}>
              <div class="legend-line" style={{ 'width': '18px', 'height': '0', 'border-top': `2px solid ${g.color}`, 'border-radius': '2px' }} />
              {g.label}
            </div>
          )}
        </For>
      </div>

      <div
        class="tooltip"
        id="tooltip"
        classList={{ visible: tooltipVisible() }}
        style={{ 'left': `${tooltipPosition().x}px`, 'top': `${tooltipPosition().y}px` }}
      >
        {/* eslint-disable-next-line solid/no-innerhtml -- content sanitized by tooltipHtml() then DOMPurify */}
        <div innerHTML={DOMPurify.sanitize(tooltipContent())} />
      </div>
    </div>
  )
}