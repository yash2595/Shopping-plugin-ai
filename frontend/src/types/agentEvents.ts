export type AgentRunState =
  | 'idle'
  | 'listening'
  | 'processing'
  | 'executing'
  | 'completed'
  | 'failed'

export type AgentEventType =
  | 'agent_started'
  | 'action_started'
  | 'action_completed'
  | 'action_failed'
  | 'navigation'
  | 'page_loaded'
  | 'agent_completed'
  | (string & {})

export type ExecutionStepStatus = 'pending' | 'running' | 'completed' | 'failed'

/**
 * Frontend contract for the future agent transport. Events are append-only so
 * WebSocket, IPC, or local mock producers can all use the same panel model.
 */
export interface AgentEvent {
  id: string
  type: AgentEventType
  message: string
  timestamp: number
  action?: string
  stepId?: string
  tabId?: string
  url?: string
  metadata?: Record<string, unknown>
}

export interface ExecutionStep {
  id: string
  message: string
  action?: string
  status: ExecutionStepStatus
  timestamp: number
}
