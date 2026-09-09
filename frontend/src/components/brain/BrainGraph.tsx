import { createEffect, onCleanup } from 'solid-js'
import { For } from 'solid-js'

interface Node {
  id: number
  name: string
  type: string
  confidence: number
}

interface Link {
  source: number
  target: number
  relationType: string
}

interface BrainGraphProps {
  onNodeClick?: (nodeId: number) => void
}

export default function BrainGraph(props: BrainGraphProps) {
  // Mock data for graph visualization - would be replaced with real API calls
  const mockNodes: Node[] = [
    { id: 1, name: 'Travel Plans', type: 'event', confidence: 0.9 },
    { id: 2, name: 'Flight Booking', type: 'process', confidence: 0.8 },
    { id: 3, name: 'Budget', type: 'concept', confidence: 0.7 },
    { id: 4, name: 'Destination', type: 'location', confidence: 0.6 },
    { id: 5, name: 'Car Rental', type: 'service', confidence: 0.85 }
  ]

  const mockLinks: Link[] = [
    { source: 1, target: 2, relationType: 'includes' },
    { source: 1, target: 3, relationType: 'based_on' },
    { source: 1, target: 4, relationType: 'located_in' },
    { source: 2, target: 5, relationType: 'requires' }
  ]

  // Simulate force-directed graph rendering
  createEffect(() => {
    console.log('Rendering brain graph with', mockNodes.length, 'nodes and', mockLinks.length, 'links')
    
    // Cleanup function (would be more complex in real implementation)
    onCleanup(() => {
      console.log('Cleaning up brain graph')
    })
  })

  const handleNodeClick = (nodeId: number) => {
    console.log('Node clicked:', nodeId)
    props.onNodeClick?.(nodeId)
  }

  return (
    <div class="brain-graph-container">
      <h3>Knowledge Graph</h3>
      
      <div class="graph-visualization">
        <div class="graph-canvas">
          {/* Visualization would be implemented using a graph library */}
          <div class="graph-placeholder">
            <p>Force-directed graph visualization</p>
            <p>Nodes: {mockNodes.length}, Links: {mockLinks.length}</p>
          </div>
          
          {/* Node placeholders with styling */}
          <div class="graph-nodes">
            <For each={mockNodes}>{node => (
              <div 
                
                class={`graph-node ${node.type}`}
                onClick={() => handleNodeClick(node.id)}
                title={`${node.name} (${node.type}) - Confidence: ${(node.confidence * 100).toFixed(0)}%`}
              >
                <span class="node-name">{node.name}</span>
                <span class="node-confidence">{(node.confidence * 100).toFixed(0)}%</span>
              </div>
            )}</For>
          </div>
          
          {/* Links between nodes */}
          <div class="graph-links">
            <For each={mockLinks}>{() => (
              <div 
                class="graph-link"
                style={{
                  'transform': `rotate(${Math.atan2(100, 150) * 180 / Math.PI}deg)`,
                  'width': '150px',
                  'height': '2px'
                }}
              />
            )}</For>
          </div>
        </div>
      </div>
      
      <div class="graph-controls">
        <button onClick={() => console.log('Zoom in')}>+</button>
        <button onClick={() => console.log('Zoom out')}>-</button>
        <button onClick={() => console.log('Reset view')}>Reset</button>
      </div>
    </div>
  )
}