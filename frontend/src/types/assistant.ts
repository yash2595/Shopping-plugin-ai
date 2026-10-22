import type { AgentEvent, AgentRunState } from './agentEvents'

export type BackendConnectionStatus = 'connected' | 'disconnected' | 'pending'

export interface SpeechRecognitionService {
  startListening: () => Promise<void>
  stopListening: () => Promise<void>
  onTranscript: (callback: (transcript: string, isFinal: boolean) => void) => () => void
  onError: (callback: (error: Error) => void) => () => void
}

export interface TextToSpeechService {
  speak: (text: string) => Promise<void>
  stop: () => void
  isSpeaking: () => boolean
}

export interface AIEngineService {
  processIntent: (
    userRequest: string,
    context: { activeTabUrl: string; activeTabTitle: string }
  ) => Promise<{
    intent: string
    plan?: string[]
  }>
  executeNextAction?: (stepId: string) => Promise<void>
}

export interface AssistantState {
  runState: AgentRunState
  isPanelOpen: boolean
  isSpeaking: boolean
  userRequest?: string
  parsedIntent?: string
  backendStatus: BackendConnectionStatus
  events: AgentEvent[]
}
