import { createEffect, createSignal, onCleanup, onMount, untrack } from 'solid-js'
import * as THREE from 'three'
import ForceGraph3D from '3d-force-graph'
import type { ConfigOptions, ForceGraph3DInstance, LinkObject, NodeObject } from '3d-force-graph'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { BrainConflict, GraphLink, GraphNode } from '../../types'
import { hexToRgba, nodeRadius, relationColor, tooltipHtml, typeColor } from './brainLib'

interface BrainGraph3DProps {
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
 * Node shape the 3D force-graph sees. GraphNode itself can't satisfy the
 * library's `NodeObject` constraint (its `fx`/`fy` are `number | null`), so
 * we hand the graph a lean copy with the fields we render.
 */
interface Graph3DNode extends NodeObject {
  id: number
  name: string
  type: string
  confidence: number
  priority: number
  essential: number
  hasConflict: boolean
  slots?: GraphNode['slots']
}

/** A graph link with the association metadata we need on top of the lib types. */
interface Graph3DLink extends LinkObject<Graph3DNode> {
  source: number
  target: number
  relationType: string
  confidence: number
}

type GraphInstance = ForceGraph3DInstance<Graph3DNode, Graph3DLink>

// The 3D force-graph derives a node's sphere radius from `radius = cbrt(val) * nodeRelSize`.
const NODE_REL_SIZE = 1.8
// Opacity applied to non-highlighted nodes; dim links bake this into their color alpha.
const DIM_ALPHA = 0.14

function to3DNodes(nodes: GraphNode[]): Graph3DNode[] {
  return nodes.map((n) => ({
    id: n.id,
    name: n.name,
    type: n.type,
    confidence: n.confidence,
    priority: n.priority,
    essential: n.essential,
    hasConflict: n.hasConflict,
    slots: n.slots,
  }))
}

function to3DLinks(links: GraphLink[]): Graph3DLink[] {
  return links.map((l) => ({
    source: l.source,
    target: l.target,
    relationType: l.relationType,
    confidence: l.confidence,
  }))
}

function sphereRadius(node: Graph3DNode): number {
  return Math.cbrt(Math.pow(nodeRadius(node), 2)) * NODE_REL_SIZE
}

function linkNodeId(id: string | number | Graph3DNode): number {
  return typeof id === 'number' ? id : Number((id as Graph3DNode).id)
}

function updateNeighborsMap(links: GraphLink[], target: Map<number, Set<number>>) {
  target.clear()
  for (const l of links) {
    if (!target.has(l.source)) target.set(l.source, new Set<number>())
    if (!target.has(l.target)) target.set(l.target, new Set<number>())
    target.get(l.source)!.add(l.target)
    target.get(l.target)!.add(l.source)
  }
}

const sphereGeometryCache = new Map<number, THREE.SphereGeometry>()
const sphereMaterialCache = new Map<string, THREE.MeshStandardMaterial>()

function sphereMesh(node: Graph3DNode): THREE.Mesh {
  const r = sphereRadius(node)
  let geometry = sphereGeometryCache.get(r)
  if (!geometry) {
    geometry = new THREE.SphereGeometry(r, 20, 14)
    sphereGeometryCache.set(r, geometry)
  }
  const hex = typeColor(node.type)
  let material = sphereMaterialCache.get(hex)
  if (!material) {
    const color = new THREE.Color(hex)
    material = new THREE.MeshStandardMaterial({
      color,
      emissive: color,
      emissiveIntensity: 0.28,
      transparent: true,
      opacity: 1,
    })
    sphereMaterialCache.set(hex, material)
  }
  return new THREE.Mesh(geometry, material)
}

/**
 * Per-node ThreeJS object: a colored sphere, plus an amber halo ring for
 * frames that have a pending memory conflict (mirrors the 2D ring).
 */
function buildNodeObject(node: Graph3DNode): THREE.Object3D {
  const sphere = sphereMesh(node)
  if (!node.hasConflict) return sphere
  const group = new THREE.Group()
  group.add(sphere)
  const r = sphereRadius(node)
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(r * 1.65, Math.max(0.25, r * 0.22), 8, 40),
    new THREE.MeshBasicMaterial({ color: 0xffb020, transparent: true, opacity: 1 }),
  )
  ring.rotation.x = Math.PI / 2.4
  group.add(ring)
  return group
}

const ForceGraph3DCtor = ForceGraph3D as unknown as new (element: HTMLElement, options?: ConfigOptions) => GraphInstance

export default function BrainGraph3D(props: BrainGraph3DProps) {
  let containerEl: HTMLDivElement | null = null
  let graph: GraphInstance | null = null
  let graphKey = ''
  let hoverNodeId: number | null = null
  let lastPointer = { x: 0, y: 0 }
  // True once the WebGL graph exists; lets the tour effect start even if the
  // user toggles Tour during the first initialization.
  const [ready, setReady] = createSignal(false)

  /** Maps the live ThreeJS objects back to node ids, for direct material dimming. */
  const nodeObjects = new Map<number, THREE.Object3D>()
  const neighborsMap = new Map<number, Set<number>>()

  const stopTourOnInteraction = () => untrack(() => {
    if (props.touring()) props.setTouring(false)
  })

  function setContainerRef(el: HTMLDivElement | null) {
    containerEl = el
  }

  /** Hover seed merged with the persistent click selection, both +1 hop. */
  function highlightedIds(): Set<number> {
    const seed = new Set<number>(props.highlightedNodeIds())
    if (hoverNodeId !== null) seed.add(hoverNodeId)
    const lit = new Set<number>()
    for (const id of seed) {
      lit.add(id)
      const nbrs = neighborsMap.get(id)
      if (nbrs) nbrs.forEach((n) => lit.add(n))
    }
    return lit
  }

  function applyHighlight() {
    if (!graph) return
    // Snapshot the selection at call time: the tracked effect re-applies
    // highlights reactively, and the force-graph sinks must not subscribe.
    const lit = untrack(() => highlightedIds())

    // Nodes: our custom objects are not re-colored by the lib, so fade them
    // directly via material opacity.
    for (const [id, obj] of nodeObjects) {
      const dimmed = !lit.has(id)
      obj.traverse((child) => {
        const mesh = child as THREE.Mesh
        const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : []
        for (const material of materials) {
          material.transparent = true
          material.opacity = dimmed ? DIM_ALPHA : 1
        }
      })
    }

    // Links: re-run the color accessor with an alpha baked into the color
    // string; the lib derives per-link opacity from it (linkOpacity * alpha).
    graph.linkColor((l) => {
      const source = linkNodeId(l.source)
      const target = linkNodeId(l.target)
      const isLit = lit.has(source) || lit.has(target)
      const base = relationColor(l.relationType)
      return isLit ? base : hexToRgba(base, 0.12)
    })
  }

  async function showNodeTooltip(node: Graph3DNode) {
    if (!node.slots) {
      try {
        const res = await fetch(`/memory/frames/${node.id}/slots`)
        node.slots = await res.json() as GraphNode['slots']
      } catch {
        node.slots = []
      }
    }
    const rect = containerEl?.getBoundingClientRect()
    if (!rect) return
    let tx = lastPointer.x - rect.left + 15
    const ty = lastPointer.y - rect.top - 10
    if (tx + 280 > rect.width) tx = lastPointer.x - rect.left - 295
    // One-shot tooltip: snapshot the current conflicts, don't subscribe.
    const conflictsByFrame = untrack(() => props.conflictsByFrame())
    untrack(() => props.onTooltip?.(tooltipHtml(node, conflictsByFrame), tx, ty))
  }

  function frameCamera(g: GraphInstance) {
    const bbox = g.getGraphBbox()
    const span = Math.max(
      bbox.x[1] - bbox.x[0],
      bbox.y[1] - bbox.y[0],
      bbox.z[1] - bbox.z[0],
      60,
    )
    const dist = span * 1.9
    g.cameraPosition({ x: 0, y: 0, z: dist }, { x: 0, y: 0, z: 0 }, 450)

    const controls = g.controls() as OrbitControls | null
    if (!controls) return
    controls.minDistance = dist * 0.18
    controls.maxDistance = dist * 3.5
    // Stop the auto-orbit tour as soon as the user grabs the graph. Dedupe the
    // listener: onEngineStop refires on every data refresh.
    controls.removeEventListener('start', stopTourOnInteraction)
    controls.addEventListener('start', stopTourOnInteraction)
  }

  function graphDataKey(): string {
    const nodes = props.nodes()
    const links = props.links()
    const nodePart = nodes
      .map((n) => `${n.id}:${n.hasConflict ? 'c' : ''}:${Math.round((n.confidence || 0) * 100)}`)
      .sort()
      .join(',')
    const linkPart = links.map((l) => `${l.source}>${l.target}:${l.relationType}`).sort().join(',')
    return `${nodes.length}|${nodePart}|${links.length}|${linkPart}`
  }

  function initGraph() {
    if (graph || !containerEl || !containerEl.isConnected || containerEl.offsetWidth === 0) return
    if (typeof WebGLRenderingContext === 'undefined') {
      props.onModeChange('2d')
      return
    }
    try {
      const nodes = props.nodes()
      const links = props.links()
      updateNeighborsMap(links, neighborsMap)
      const g = new ForceGraph3DCtor(containerEl, { controlType: 'orbit' })
      graph = g
      graphKey = graphDataKey()

      g
        .width(props.width())
        .height(props.height())
        .backgroundColor('rgba(0,0,0,0)')
        .showNavInfo(false)
        .nodeId('id')
        .nodeLabel((n) => n.name)
        .nodeThreeObject((node) => {
          const obj = buildNodeObject(node)
          // Track live objects so applyHighlight can dim node materials directly.
          nodeObjects.set(node.id, obj)
          return obj
        })
        .linkWidth((l) => Math.max(0.5, (l.confidence ?? 0.5) * 1.6))
        .linkOpacity(0.35)
        .cooldownTicks(90)
        .d3AlphaDecay(0.05)
        .d3VelocityDecay(0.3)
        .onNodeClick((node) => {
          untrack(() => props.onNodeClick?.(node))
          void showNodeTooltip(node)
        })
        .onNodeHover((node) => {
          hoverNodeId = node ? Number(node.id) : null
          if (node) {
            void showNodeTooltip(node)
          } else {
            untrack(() => props.onHideTooltip?.())
          }
          applyHighlight()
        })
        .onBackgroundClick(() => untrack(() => props.onHideTooltip?.()))
        .onNodeDragEnd(() => untrack(() => applyHighlight()))
        .onEngineStop(() => untrack(() => frameCamera(g)))

      g.graphData({ nodes: to3DNodes(nodes), links: to3DLinks(links) })
      applyHighlight()
      setReady(true)
    } catch (err) {
      console.error('3D graph init failed:', err)
      try {
        graph?._destructor()
      } catch {
        // ignore — context may already be gone
      }
      graph = null
      setReady(false)
      props.onModeChange('2d')
    }
  }

  // Initialize once the container has real dimensions (webgl needs a laid-out box).
  onMount(() => {
    const checkReady = () => {
      // Stop polling when the component unmounts (e.g. switching back to 2D).
      if (!containerEl || !containerEl.isConnected) return
      if (containerEl.offsetWidth > 0 && containerEl.offsetHeight > 0) {
        initGraph()
      } else {
        requestAnimationFrame(checkReady)
      }
    }
    checkReady()
  })

  onCleanup(() => {
    try {
      graph?._destructor()
    } catch {
      // ignore — context may already be gone
    }
    graph = null
    setReady(false)
  })

  // Resync the 3D graph when memory data changes (new frames/associations/conflicts).
  createEffect(() => {
    if (!graph) return
    const key = graphDataKey()
    if (key === graphKey) return
    graphKey = key
    nodeObjects.clear()
    const nodes = props.nodes()
    const links = props.links()
    updateNeighborsMap(links, neighborsMap)
    graph.graphData({ nodes: to3DNodes(nodes), links: to3DLinks(links) })
    applyHighlight()
  })

  createEffect(() => {
    const w = props.width()
    const h = props.height()
    graph?.width(w).height(h)
  })

  createEffect(() => {
    props.highlightedNodeIds()
    applyHighlight()
  })

  // Auto-orbit tour while `touring` is on; stops the moment the user interacts.
  createEffect(() => {
    if (!props.touring() || !ready()) return
    const current = graph
    if (!current) return
    const controlsRef = current.controls() as OrbitControls | null
    if (!controlsRef) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      if (!props.touring()) return
      const ctrl = current.controls() as OrbitControls | null
      if (!ctrl) return
      ctrl.rotateLeft((now - last) * 0.00022)
      ctrl.update()
      last = now
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    onCleanup(() => cancelAnimationFrame(raf))
  })

  // Tooltip positioning follows the pointer over the canvas.
  createEffect(() => {
    const el = containerEl
    if (!el) return
    const track = (e: MouseEvent) => {
      lastPointer = { x: e.clientX, y: e.clientY }
    }
    el.addEventListener('mousemove', track)
    onCleanup(() => el.removeEventListener('mousemove', track))
  })

  return <div ref={setContainerRef} id="brain-canvas-3d" class="brain-canvas-3d" />
}