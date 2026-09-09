import { createSignal } from 'solid-js'
import BrainGraph from './BrainGraph'
import FrameDetail from './FrameDetail'
import SearchPanel from './SearchPanel'

interface Frame {
  id: number
  name: string
  type: string
  confidence: number
  essential: boolean
  priority: number
  ownerUserId: number
  sourceType: string
  sourceUrl: string
  sourceReliability: number
  createdAt: string
  updatedAt: string
  slots: Slot[]
  associations: Association[]
}

interface Slot {
  key: string
  value: string
  confidence: number
  sourceType: string
  updatedAt: string
}

interface Association {
  id: number
  relationType: string
  targetFrameId: number
}

export default function BrainPage() {
  const [selectedFrame, setSelectedFrame] = createSignal<Frame | null>(null)

  // Mock data - in real app this would come from API
  const mockFrames: Frame[] = [
    { id: 1, name: 'Travel Plans', type: 'event' },
    { id: 2, name: 'Flight Booking', type: 'process' },
    { id: 3, name: 'Budget', type: 'concept' }
  ]

  const handleNodeClick = (nodeId: number) => {
    console.log('Node clicked:', nodeId)
    
    // Would fetch real frame data
    const mockFrame = mockFrames.find(f => f.id === nodeId)
    if (mockFrame) {
      setSelectedFrame({
        ...mockFrame,
        confidence: 0.85,
        essential: true,
        priority: 0.7,
        ownerUserId: 1,
        sourceType: 'web_search',
        sourceUrl: 'https://example.com',
        sourceReliability: 0.9,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        slots: [
          { key: 'destination', value: 'Paris', confidence: 0.92, sourceType: 'search', updatedAt: new Date().toISOString() },
          { key: 'dates', value: 'June 15-22', confidence: 0.85, sourceType: 'user_input', updatedAt: new Date().toISOString() }
        ],
        associations: [
          { id: 1, relationType: 'includes', targetFrameId: 2 },
          { id: 2, relationType: 'based_on', targetFrameId: 3 }
        ]
      })
    }
  }

  const handleResultClick = (resultId: number) => {
    console.log('Search result clicked:', resultId)
    // Would navigate to frame related to this search result
  }

  const handleBack = () => {
    setSelectedFrame(null)
  }

  return (
    <div class="brain-page">
      <div class="brain-header">
        <h2>Knowledge Observatory</h2>
        <p>Explore your assistant's memory and understanding</p>
      </div>
      
      <div class="brain-content">
        <div class="brain-sidebar">
          <SearchPanel onResultClick={handleResultClick} />
        </div>
        
        <div class="brain-main">
          {selectedFrame() ? (
            <FrameDetail frame={selectedFrame()} onBack={handleBack} />
          ) : (
            <BrainGraph onNodeClick={handleNodeClick} />
          )}
        </div>
      </div>
    </div>
  )
}