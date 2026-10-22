import type { AgentEvent, ExecutionStep, ExecutionStepStatus } from '../types/agentEvents'

const eventStatus: Partial<Record<AgentEvent['type'], ExecutionStepStatus>> = {
  action_started: 'running',
  action_completed: 'completed',
  action_failed: 'failed',
  navigation: 'completed',
  page_loaded: 'completed',
}

export function getExecutionSteps(events: AgentEvent[]): ExecutionStep[] {
  const steps = new Map<string, ExecutionStep>()

  for (const event of events) {
    const status = eventStatus[event.type]
    if (!status) continue

    const id = event.stepId || event.id
    const existing = steps.get(id)
    steps.set(id, {
      id,
      message: event.message || existing?.message || 'Agent action',
      action: event.action || existing?.action,
      status,
      timestamp: event.timestamp,
    })
  }

  return Array.from(steps.values()).sort((a, b) => a.timestamp - b.timestamp)
}

export function getCurrentAction(events: AgentEvent[]): ExecutionStep | undefined {
  const running = getExecutionSteps(events).findLast((step) => step.status === 'running')
  return running
}
