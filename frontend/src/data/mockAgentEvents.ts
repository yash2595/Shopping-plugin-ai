import type { AgentEvent } from '../types/agentEvents'

type MockEventTemplate = Omit<AgentEvent, 'id' | 'timestamp'> & { delay: number }

export const MOCK_FLIGHT_REQUEST = 'Find monuments of Delhi'
export const MOCK_FLIGHT_INTENT = 'Search and compare relevant monuments and heritage sources in Delhi'

export const MOCK_FLIGHT_EXECUTION: MockEventTemplate[] = [
  { delay: 0, type: 'agent_started', message: 'Starting local flight-search demo' },
  { delay: 350, type: 'action_started', action: 'prepare', stepId: 'prepare-request', message: 'Prepare flight search request' },
  { delay: 1100, type: 'action_completed', action: 'prepare', stepId: 'prepare-request', message: 'Prepare flight search request' },
  { delay: 1450, type: 'action_started', action: 'review', stepId: 'review-request', message: 'Review Mumbai to Delhi request' },
  { delay: 2250, type: 'action_completed', action: 'review', stepId: 'review-request', message: 'Review Mumbai to Delhi request' },
  { delay: 2600, type: 'agent_completed', message: 'Request prepared; browser remains on Google homepage' },
]

export function createMockEvent(template: MockEventTemplate): AgentEvent {
  return {
    id: `${template.type}-${template.stepId || 'agent'}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    type: template.type,
    action: template.action,
    stepId: template.stepId,
    message: template.message,
    timestamp: Date.now(),
  }
}
