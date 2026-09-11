import { createSignal, createEffect, onCleanup, createMemo, onMount } from 'solid-js'
import { For } from 'solid-js'
import * as d3 from 'd3'

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
}

interface Link {
  source: number
  target: number
  relationType: string
  confidence: number
}

interface BrainGraphProps {
  nodes: Node[]
  links: Link[]
  width: number
  height: number
  onNodeClick?: (node: Node) => void
  highlightedNodeIds: Set<number>
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

export default function BrainGraph(props: BrainGraphProps) {
  const [svgRef, setSvgRef] = createSignal<SVGSVGElement | null>(null)
  const [simulation, setSimulation] = createSignal<d3.Simulation<Node, Link> | null>(null)

  const nodesWithPos = createMemo(() => 
    props.nodes.map((n) => ({
      ...n,
      x: n.x ?? props.width / 2 + (Math.random() - 0.5) * 200,
      y: n.y ?? props.height / 2 + (Math.random() - 0.5) * 200,
      vx: 0,
      vy: 0,
      fx: n.fx ?? null,
      fy: n.fy ?? null,
    }))
  )

  onMount(() => {
    const svg = svgRef()
    if (!svg) return

    const container = d3.select(svg)
    const g = container.append('g').attr('class', 'graph')

    const defs = container.append('defs')

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

    const sim = d3.forceSimulation<Node, Link>(nodesWithPos())
      .force('link', d3.forceLink<Link, Node>(props.links).id((d: Node) => d.id).distance(100).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(props.width / 2, props.height / 2))
      .force('collision', d3.forceCollide<Node>().radius((d: Node) => nodeRadius(d) + 8))

    setSimulation(sim)

    const edgeColors = [...new Set(props.links.map(l => relationColor(l.relationType)))]
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

    const link = g.append('g')
      .attr('class', 'links')
      .selectAll('path')
      .data(props.links)
      .join('path')
      .attr('class', 'link')
      .attr('stroke', (d: Link) => relationColor(d.relationType))
      .attr('stroke-width', (d: Link) => {
        const conf = d.confidence || 0.5
        return conf === 0.5 ? 1.25 : Math.max(0.75, 2.25 * conf)
      })
      .attr('marker-end', (d: Link) => `url(#arrow-${relationColor(d.relationType).slice(1)})`)

    const edgeLabel = g.append('g')
      .attr('class', 'edge-labels')
      .selectAll('text')
      .data(props.links)
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

    node.append('title')
      .text((d: Node) => `${d.name} (${d.type})\nConfidence: ${Math.round((d.confidence || 0.5) * 100)}%`)

    sim.on('tick', () => {
      link.attr('d', edgePath)
      edgeLabel
        .attr('x', (d: d3.SimulationLinkDatum<Link>) => edgeMidpoint(d).x)
        .attr('y', (d: d3.SimulationLinkDatum<Link>) => edgeMidpoint(d).y)
      node.attr('transform', (d: Node) => `translate(${d.x},${d.y})`)
    })

    const neighborsMap = new Map<number, Set<number>>()
    props.links.forEach(l => {
      if (!neighborsMap.has(l.source)) neighborsMap.set(l.source, new Set())
      if (!neighborsMap.has(l.target)) neighborsMap.set(l.target, new Set())
      neighborsMap.get(l.source)!.add(l.target)
      neighborsMap.get(l.target)!.add(l.source)
    })

    const handleMouseOver = (event: MouseEvent, d: Node) => {
      highlightNeighbors(d.id)
      props.onNodeClick?.(d)
    }

    const handleMouseOut = () => {
      clearHighlight()
      applyHighlight(computeHighlightSet())
    }

    const handleClick = (_event: MouseEvent, d: Node) => {
      props.onNodeClick?.(d)
    }

    node.on('mouseover', handleMouseOver)
      .on('mouseout', handleMouseOut)
      .on('click', handleClick)

    function highlightNeighbors(activeId: number) {
      const nbrs = neighborsMap.get(activeId) || new Set()
      link.classed('dimmed', (l: d3.SimulationLinkDatum<Link>) => l.source.id !== activeId && l.target.id !== activeId)
        .classed('lit', (l: d3.SimulationLinkDatum<Link>) => l.source.id === activeId || l.target.id === activeId)
      edgeLabel.classed('visible', (l: d3.SimulationLinkDatum<Link>) => l.source.id === activeId || l.target.id === activeId)
      node.classed('dimmed', (n: Node) => n.id !== activeId && !nbrs.has(n.id))
        .classed('lit', (n: Node) => nbrs.has(n.id))
    }

    function computeHighlightSet(): Set<number> {
      const lit = new Set<number>()
      if (props.highlightedNodeIds.size === 0) return lit
      for (const id of props.highlightedNodeIds) {
        lit.add(id)
        const nbrs = neighborsMap.get(id)
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

    applyHighlight(computeHighlightSet())

    onCleanup(() => {
      sim.stop()
      node.on('mouseover', null).on('mouseout', null).on('click', null)
    })
  })

  createEffect(() => {
    const sim = simulation()
    if (!sim) return
    sim.force('center', d3.forceCenter(props.width / 2, props.height / 2))
    sim.alpha(0.3).restart()
  })

  return (
    <svg ref={setSvgRef} width={props.width} height={props.height} class="brain-canvas">
      <rect width="100%" height="100%" fill="var(--bg)" />
      <g class="stars">
        <For each={Array.from({ length: 200 })}>
          {(_, index) => (
            <circle
              key={index}
              cx={Math.random() * props.width}
              cy={Math.random() * props.height}
              r={Math.random() * 0.8 + 0.2}
              fill="white"
              opacity={Math.random() * 0.4 + 0.1}
            />
          )}
        </For>
      </g>
      <g class="graph" />
    </svg>
  )
}