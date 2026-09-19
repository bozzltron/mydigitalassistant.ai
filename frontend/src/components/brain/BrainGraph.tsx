import { createSignal, createEffect, onCleanup, createMemo, Show, lazy, Suspense } from 'solid-js'
import { For } from 'solid-js'
import * as d3 from 'd3'

interface Conflict {
  frame_id: number
  frame_name: string
  slot_key: string
  slot_value: string
  confidence: number
  new_value: string
  new_confidence: number
  created_at: string
}

interface Node {
  id: number
  name: string
  type: string
  confidence: number
  priority: number
  essential: boolean
  x: number
  y: number
  vx: number
  vy: number
  fx: number | null
  fy: number | null
  hasConflict: boolean
  slots?: Array<{ key: string; value: string; confidence?: number }>
}

interface Link {
  source: number
  target: number
  relationType: string
  confidence: number
}

interface BrainGraphProps {
  nodes: () => Node[]
  links: () => Link[]
  width: () => number
  height: () => number
  onNodeClick?: (node: Node) => void
  highlightedNodeIds: () => Set<number>
  conflictsByFrame: () => Record<number, Conflict[]>
  mode: () => '2d' | '3d'
  onModeChange: (mode: '2d' | '3d') => void
  touring: () => boolean
  setTouring: (touring: boolean) => void
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

function edgePath(d: d3.SimulationLinkDatum<Link>): string {
  const sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y
  const dx = tx - sx, dy = ty - sy
  const dist = Math.sqrt(dx * dx + dy * dy) || 1
  const cx = sx + dx / 2 - (dy / dist) * dist * 0.08
  const cy = sy + dy / 2 + (dx / dist) * dist * 0.08
  return `M${sx},${sy}Q${cx},${cy}${tx},${ty}`
}

function edgeMidpoint(d: d3.SimulationLinkDatum<Link>): { x: number; y: number } {
  const sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y
  const dx = tx - sx, dy = ty - sy
  const dist = Math.sqrt(dx * dx + dy * dy) || 1
  const cx = sx + dx / 2 - (dy / dist) * dist * 0.08
  const cy = sy + dy / 2 + (dx / dist) * dist * 0.08
  return {
    x: (sx + 2 * cx + tx) / 4,
    y: (sy + 2 * cy + ty) / 4 + 3,
  }
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

  const frameConflicts = conflictsByFrame[d.id] || []
  const conflictsHtml = frameConflicts.map(c => `
    <div class="tooltip-conflict">
      <div class="tooltip-conflict-line">
        <span class="tooltip-conflict-key">${esc(c.slot_key)}</span>:
        <span class="tooltip-conflict-old">${esc(c.existing_value ?? '∅')}</span>
        →
        <span class="tooltip-conflict-new">${esc(c.new_value ?? '∅')}</span>
      </div>
      <div class="tooltip-conflict-actions">
        <button onclick="resolveConflict(${c.id}, '${escAttr(c.existing_value)}')">Keep ${esc(c.existing_value ?? 'old')}</button>
        <button class="use-new" onclick="resolveConflict(${c.id}, '${escAttr(c.new_value)}')">Use ${esc(c.new_value ?? 'new')}</button>
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


// Lazy-loaded 3D component (Sigma.js WebGL)
const BrainGraph3D = lazy(() => import('./BrainGraphSigma').then(m => ({ default: m.default })))

export default function BrainGraph(props: BrainGraphProps) {
  const [svgRef, setSvgRef] = createSignal<SVGSVGElement | null>(null)
  const [svgReady, setSvgReady] = createSignal(false)
  const [simulation, setSimulation] = createSignal<d3.Simulation<Node, Link> | null>(null)
  const [gSelection, setGSelection] = createSignal<d3.Selection<SVGGElement, unknown, null, undefined> | null>(null)
  const [defsSelection, setDefsSelection] = createSignal<d3.Selection<SVGDefsElement, unknown, null, undefined> | null>(null)
  const [tooltipVisible, setTooltipVisible] = createSignal(false)
  const [tooltipContent, setTooltipContent] = createSignal('')
  const [tooltipPosition, setTooltipPosition] = createSignal({ x: 0, y: 0 })

  const nodesWithPos = createMemo(() =>
    props.nodes().map((n) => ({
      ...n,
      x: n.x ?? props.width() / 2 + (Math.random() - 0.5) * 200,
      y: n.y ?? props.height() / 2 + (Math.random() - 0.5) * 200,
      vx: 0,
      vy: 0,
      fx: n.fx ?? null,
      fy: n.fy ?? null,
    }))
  )

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

  function render2D() {
    const svg = svgRef()
    if (!svg) return

    const container = d3.select(svg)
    const w = props.width()
    const h = props.height()

    container.attr('width', w).attr('height', h)

    let g = gSelection()
    let defs = defsSelection()

    if (!g) {
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

      const zoom = d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
          g.attr('transform', event.transform)
        })
      container.call(zoom)
    }

    g.selectAll('*').remove()

    if (simulation()) simulation()!.stop()

    const sim = d3.forceSimulation<Node, Link>(nodesWithPos())
      .force('link', d3.forceLink<Link, Node>(props.links()).id((d: Node) => d.id).distance(100).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(w / 2, h / 2))
      .force('collision', d3.forceCollide<Node>().radius((d: Node) => nodeRadius(d) + 8))

    setSimulation(sim)

    buildEdgeLegend(defs!)

    const newNeighborsMap = new Map<number, Set<number>>()
    props.links().forEach(l => {
      if (!newNeighborsMap.has(l.source)) newNeighborsMap.set(l.source, new Set())
      if (!newNeighborsMap.has(l.target)) newNeighborsMap.set(l.target, new Set())
      newNeighborsMap.get(l.source)!.add(l.target)
      newNeighborsMap.get(l.target)!.add(l.source)
    })
    setNeighborsMap(newNeighborsMap)

    const link = g.append('g')
      .attr('class', 'links')
      .selectAll('path')
      .data(props.links())
      .join('path')
      .attr('class', 'link')
      .attr('stroke', (d: Link) => relationColor(d.relationType))
      .attr('stroke-width', (d: Link) => {
        const conf = d.confidence || 0.5
        return conf === 0.5 ? 1.25 : Math.max(0.75, 2.25 * conf)
      })
      .attr('marker-end', (d: Link) => `url(#arrow-${relationColor(d.relationType).slice(1)})`)

    link.append('title')
      .text((d: Link) => `${d.relationType} (${Math.round((d.confidence || 0.5) * 100)}%)`)

    const edgeLabel = g.append('g')
      .attr('class', 'edge-labels')
      .selectAll('text')
      .data(props.links())
      .join('text')
      .attr('class', 'edge-label')
      .attr('text-anchor', 'middle')
      .text((d: Link) => d.relationType)

    const node = g.append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(nodesWithPos())
      .join('g')
      .attr('class', (d: Node) => d.hasConflict ? 'node has-conflict' : 'node')
      .call(d3.drag<SVGGElement, Node>()
        .on('start', (event: d3.D3DragEvent<SVGGElement, Node, Node>, d: Node) => {
          if (!event.active) sim.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on('drag', (event: d3.D3DragEvent<SVGGElement, Node, Node>, d: Node) => {
          d.fx = event.x
          d.fy = event.y
        })
        .on('end', (event: d3.D3DragEvent<SVGGElement, Node, Node>, d: Node) => {
          if (!event.active) sim.alphaTarget(0)
          d.fx = null
          d.fy = null
        }))

    node.append('circle')
      .attr('r', (d: Node) => nodeRadius(d))
      .attr('fill', (d: Node) => TYPE_COLORS[d.type] || TYPE_COLORS.entity)
      .attr('filter', (d: Node) => d.hasConflict ? 'url(#strongGlow)' : 'url(#glow)')
      .attr('class', (d: Node) => d.hasConflict ? 'conflict-pulse' : '')

    sim.on('tick', () => {
      link.attr('d', edgePath)
      edgeLabel
        .attr('x', (d: d3.SimulationLinkDatum<Link>) => edgeMidpoint(d).x)
        .attr('y', (d: d3.SimulationLinkDatum<Link>) => edgeMidpoint(d).y)
      node.attr('transform', (d: Node) => `translate(${d.x},${d.y})`)
    })

    function highlightNeighbors(activeId: number) {
      const nbrs = newNeighborsMap.get(activeId) || new Set()
      link.classed('dimmed', (l: d3.SimulationLinkDatum<Link>) => l.source.id !== activeId && l.target.id !== activeId)
        .classed('lit', (l: d3.SimulationLinkDatum<Link>) => l.source.id === activeId || l.target.id === activeId)
      edgeLabel.classed('visible', (l: d3.SimulationLinkDatum<Link>) => l.source.id === activeId || l.target.id === activeId)
      node.classed('dimmed', (n: Node) => n.id !== activeId && !nbrs.has(n.id))
        .classed('lit', (n: Node) => nbrs.has(n.id))
    }

    function computeHighlightSet(): Set<number> {
      const lit = new Set<number>()
      if (props.highlightedNodeIds().size === 0) return lit
      for (const id of props.highlightedNodeIds()) {
        lit.add(id)
        const nbrs = newNeighborsMap.get(id)
        if (nbrs) nbrs.forEach(n => lit.add(n))
      }
      return lit
    }

    function applyHighlight(litIds: Set<number>) {
      if (litIds.size === 0) {
        clearHighlight()
        return
      }
      link.classed('dimmed', (l: d3.SimulationLinkDatum<Link>) => !litIds.has(l.source.id) && !litIds.has(l.target.id))
        .classed('lit', (l: d3.SimulationLinkDatum<Link>) => litIds.has(l.source.id) || litIds.has(l.target.id))
      edgeLabel.classed('visible', (l: d3.SimulationLinkDatum<Link>) => litIds.has(l.source.id) || litIds.has(l.target.id))
      node.classed('dimmed', (n: Node) => !litIds.has(n.id))
        .classed('lit', (n: Node) => litIds.has(n.id))
    }

    function clearHighlight() {
      link.classed('dimmed', false).classed('lit', false)
      node.classed('dimmed', false).classed('lit', false)
      edgeLabel.classed('visible', false)
    }

    async function fetchSlots(d: Node): Promise<Node> {
      if (!d.slots) {
        try {
          const res = await fetch(`/memory/frames/${d.id}/slots`)
          d.slots = await res.json()
        } catch {
          d.slots = []
        }
      }
      return d
    }

    function showTooltip(event: MouseEvent, d: Node) {
      fetchSlots(d).then(dWithSlots => {
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

    node.on('mouseover', (event: MouseEvent, d: Node) => {
      highlightNeighbors(d.id)
      showTooltip(event, d)
    })
      .on('mouseout', () => {
        clearHighlight()
        applyHighlight(computeHighlightSet())
        hideTooltip()
      })
      .on('click', (event: MouseEvent, d: Node) => {
        showTooltip(event, d)
        props.onNodeClick?.(d)
      })

    applyHighlight(computeHighlightSet())

    onCleanup(() => {
      sim.stop()
      node.on('mouseover', null).on('mouseout', null).on('click', null)
    })
  }

  // Resize handling for 2D
  createEffect(() => {
    props.width()
    props.height()
    if (props.mode() === '2d' && svgRef() && simulation()) {
      const sim = simulation()!
      sim.force('center', d3.forceCenter(props.width() / 2, props.height() / 2))
      sim.alpha(0.3).restart()
    }
  })

  // Initialize/cleanup 2D when mode changes
  createEffect(() => {
    const mode = props.mode()
    if (mode === '2d') {
      // Will initialize when svgReady becomes true
    } else {
      if (simulation()) simulation()!.stop()
    }
  })

  // Initialize 2D when SVG ref is ready
  createEffect(() => {
    if (props.mode() === '2d' && svgRef()) {
      const svg = svgRef()
      if (svg && svg.isConnected && svg.clientWidth > 0) {
        setSvgReady(true)
        render2D()
      }
    } else {
      setSvgReady(false)
    }
  })

  // Highlight updates for 2D
  createEffect(() => {
    if (props.mode() === '2d' && svgReady()) {
      applyHighlight(computeHighlightSet())
    }
  })

  onCleanup(() => {
    const sim = simulation()
    if (sim) sim.stop()
  })

  const is2d = props.mode() === '2d'
  const is3d = props.mode() === '3d'

  return (
    <div class="brain-graph-wrapper" style={{ position: 'relative', width: '100%', height: '100%', flex: '1' }}>
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
          <g class="graph" />
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
          />
        </Suspense>
      </Show>

      <div class="edge-legend" style={{ position: 'absolute', left: '1rem', bottom: '3.2rem', zIndex: 10 }}>
        <For each={RELATION_GROUPS}>
          {(g) => (
            <div class="legend-item" style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', fontSize: '0.72rem', color: 'var(--text-dim)' }}>
              <div class="legend-line" style={{ width: '18px', height: '0', borderTop: `2px solid ${g.color}`, borderRadius: '2px' }} />
              {g.label}
            </div>
          )}
        </For>
      </div>

      <Show when={tooltipVisible()}>
        <div
          class="tooltip visible"
          style={{
            left: `${tooltipPosition().x}px`,
            top: `${tooltipPosition().y}px`,
          }}
        >
          {/* eslint-disable-next-line solid/no-innerhtml -- content sanitized by tooltipHtml() */}
          <div innerHTML={tooltipContent()} />
        </div>
      </Show>
    </div>
  )
}