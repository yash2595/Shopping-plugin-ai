import type { AgentEvent, AgentRunState } from './agentEvents'

export interface AnalysisProduct {
  product: RuntimeProduct
  source_url: string
  reviews: { review_id: string; rating: number; text: string; verified_purchase_observed?: boolean | null }[]
}

export interface ShoppingAnalysis {
  status: 'answered' | 'needs_input' | 'no_evidence'
  answer: string
  evidence: { evidence_id: string; product_id: string; product_title: string; snapshot_id: string; document_id: string;
    source_url: string; observed_at: string; kind: string; label: string; value: string;
    review_id: string | null; rating: number | null; verified_purchase_observed: boolean | null }[]
  comparison: { label: string; values: Record<string, string | null>; evidence_ids: Record<string, string | null> }[]
  shortlist: string[]
  lowest_observed_price_product_id: string | null
  review_sample_size: number
  review_match_count: number
  coverage: string
  warnings: string[]
  follow_up: string | null
  skills: { skill: 'highlight_spec' | 'scroll_to_reviews'; product_id: string;
    document_id: string; source_url: string; spec_key: string | null; review_id: string | null }[]
  processing: 'local_extractive' | 'grounded_model_selection' | 'local_capture_fallback'
  inference_fallback?: 'deterministic' | 'cloud' | null
}

export interface ConversationTurn { id: string; role: 'user' | 'assistant'; text: string }
export interface ShoppingPanelIntegration {
  conversation?: ConversationTurn[]
  onVoiceListening?: (listening: boolean) => void
  analysis?: ShoppingAnalysis | null
  analysisError?: string
  onAskShopping?: (question: string) => Promise<void>
  onStopShopping?: () => void
}

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

/** AI transport contracts; browser executors must validate work before acting. */
export interface RuntimeTarget {
  tab_id: number
  document_id: string
  active: boolean
  window_focused: boolean
}

export interface RuntimeProduct {
  snapshot_id: string
  product_id: string
  document_id: string
  title: string
  observed_at: string
  selected_variant_id: string | null
  variants: { variant_id: string; label: string }[]
  price: string | null
  currency: string | null
  in_stock: boolean | null
  specs: Record<string, string>
}

export interface RuntimeReply {
  inference_fallback: 'deterministic' | 'cloud' | null
  conversation_id: string
  session_id: string
  version: number
  event_id: string | null
  status: 'chat' | 'needs_input' | 'ready_for_search' | 'ready_to_navigate' | 'awaiting_browser' |
    'working' | 'complete' | 'paused' | 'cancelled' | 'error'
  message: string
  speech_text: string
  speech_available: boolean
  products: RuntimeProduct[]
  facts: { fact_id: string; key: string; value: string; snapshot_id: string }[]
  work_ids: string[]
  origin: string | null
  comparison: unknown
  market_result: unknown
  market_answer: unknown
}

export interface RuntimeEvent {
  event_id: string
  expected_version: number
  kind: 'text' | 'cancel' | 'advance' | 'resume'
  message?: string
  target?: RuntimeTarget
  origin?: string
}

export interface RuntimeConfirmation {
  event_id: string
  expected_version: number
  confirmed: true
  operation: 'select_variant' | 'add_to_cart'
  snapshot_id: string
  product_id: string
  variant_id: string
  quantity: number
  mode: 'ensure_quantity' | 'add_quantity'
  max_unit_price: string
  currency: string
  target: RuntimeTarget
  origin: string
}

export interface RuntimeCapabilities {
  provider: 'mock' | 'chat' | 'local'
  analysis_only: boolean
  local_fallback: 'deterministic' | 'cloud' | null
  authentication: 'anonymous' | 'oidc'
  mutations_enabled: boolean
  browser_origins: string[]
  speech_to_text_configured: boolean
  text_to_speech_configured: boolean
  limits: { json_bytes: number; audio_bytes: number; events_per_user_per_day: number; speech_per_user_per_day: number }
}

export interface RuntimeWork {
  dispatch_id: string
  kind: 'browser' | 'market' | 'navigation'
  status: 'pending' | 'claimed'
  version: number
  deadline_at: string
}

export interface ClaimedRuntimeWork extends Omit<RuntimeWork, 'status'> {
  claim_token: string
  origin: string
  work: Record<string, unknown>
  constraints: { max_unit_price: string; currency: string } | null
}

export interface RuntimeReceipt {
  event_id: string
  expected_version: number
  claim_token: string
  origin: string
  browser?: {
    outcome: 'observed' | 'failed' | 'unknown'
    target: RuntimeTarget
    observed_at: string
    product?: RuntimeProduct
    cart?: { observed_at: string; document_id: string; lines: { product_id: string; variant_id: string; quantity: number }[] }
    cart_coverage?: 'complete' | 'partial' | 'unknown'
    error_code?: string
  }
  search?: { status: 'complete' | 'failed' | 'blocked' | 'timed_out'; offers: Record<string, unknown>[];
    target: RuntimeTarget; observed_at: string }
  navigation?: Record<string, unknown>
}
