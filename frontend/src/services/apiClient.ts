/**
 * Backend API Client for Copilot Browser.
 * Integrates with Amita's FastAPI shopping-plugin backend.
 *
 * Security: Uses Electron IPC secure token vault when available, with an in-memory
 * fallback. Does NOT use localStorage for sensitive authentication bearer tokens.
 */

import type {
  ClaimedRuntimeWork, RuntimeCapabilities, RuntimeConfirmation, RuntimeEvent, RuntimeReceipt, RuntimeReply,
  RuntimeTarget, RuntimeWork,
} from '../types/assistant'

export interface RegisterResponse {
  access_token: string
  token_type: string
  expires_at: string
}

export interface SessionCreate {
  title?: string
  carry_context_from?: string
}

export interface SessionRead {
  id: string
  title?: string | null
  created_at: string
  updated_at: string
}

export interface PriceAlertRead {
  id: string
  target_price: number
  active: boolean
  created_at: string
}

export interface MessageCreate {
  request_id: string
  snapshot_id?: string
  text: string
}

export interface MessageRead {
  id: string
  session_id: string
  role: string
  content: string
  kind?: string
  alert?: PriceAlertRead | null
  request_id?: string | null
  created_at: string
}

export interface SessionDetailRead extends SessionRead {
  messages: MessageRead[]
}

export interface SnapshotCreate {
  retailer: string
  retailer_product_id: string
  title: string
  url?: string
  price: number
  availability: string
  document_id?: string
  missing_fields?: string[]
}

export interface PriceSummary {
  verdict: string
  reason?: string | null
  tracking_since?: string | null
  lowest_price?: number | null
  highest_price?: number | null
  average_price?: number | null
  observation_count: number
}

export interface SnapshotResponse {
  snapshot_id: string
  listing_id: string
  price_recorded: boolean
  price_summary: PriceSummary
}

export class ApiError extends Error {
  public readonly statusCode: number
  public readonly detail?: unknown
  public readonly code?: string

  constructor(
    message: string,
    statusCode: number,
    detail?: unknown,
    code?: string
  ) {
    super(message)
    this.name = 'ApiError'
    this.statusCode = statusCode
    this.detail = detail
    this.code = code
  }
}

export class AuthError extends ApiError {
  constructor(message = 'Authentication failed', detail?: unknown) {
    super(message, 401, detail, 'AUTH_ERROR')
    this.name = 'AuthError'
  }
}

export class NotFoundError extends ApiError {
  constructor(message = 'Resource not found', detail?: unknown) {
    super(message, 404, detail, 'NOT_FOUND')
    this.name = 'NotFoundError'
  }
}

export class RateLimitError extends ApiError {
  constructor(message = 'Daily AI limit exceeded', detail?: unknown) {
    super(message, 429, detail, 'RATE_LIMITED')
    this.name = 'RateLimitError'
  }
}

export class NetworkError extends Error {
  public readonly originalError?: unknown

  constructor(message: string, originalError?: unknown) {
    super(message)
    this.name = 'NetworkError'
    this.originalError = originalError
  }
}

export interface TokenProvider {
  get: () => Promise<string | null>
  set: (token: string) => Promise<boolean | void>
  clear: () => Promise<boolean | void>
}

export interface ApiClientConfig {
  baseUrl?: string
  customFetch?: typeof fetch
  tokenProvider?: TokenProvider
  allowAnonymousRegistration?: boolean
}

// In-memory fallback if Electron IPC is not accessible (e.g. unit tests or standalone browser mode)
let memoryTokenVault: string | null = null

export const defaultTokenProvider: TokenProvider = {
  get: async () => {
    if (typeof window !== 'undefined' && window.electronAPI?.getAuthToken) {
      try {
        return await window.electronAPI.getAuthToken()
      } catch {
        return memoryTokenVault
      }
    }
    return memoryTokenVault
  },
  set: async (token: string) => {
    if (typeof window !== 'undefined' && window.electronAPI?.setAuthToken) {
      try {
        await window.electronAPI.setAuthToken(token)
      } catch {
        // ignore and set memory vault
      }
    }
    memoryTokenVault = token
  },
  clear: async () => {
    if (typeof window !== 'undefined' && window.electronAPI?.clearAuthToken) {
      try {
        await window.electronAPI.clearAuthToken()
      } catch {
        // ignore
      }
    }
    memoryTokenVault = null
  },
}

export class ShoppingApiClient {
  public readonly baseUrl: string
  private readonly fetchFn: typeof fetch
  private readonly tokenProvider: TokenProvider
  private readonly allowAnonymousRegistration: boolean

  constructor(config: ApiClientConfig = {}) {
    this.baseUrl = (config.baseUrl || 'http://localhost:8000').replace(/\/+$/, '')
    this.fetchFn = config.customFetch || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : (undefined as unknown as typeof fetch))
    this.tokenProvider = config.tokenProvider || defaultTokenProvider
    this.allowAnonymousRegistration = config.allowAnonymousRegistration ?? true
  }

  /**
   * Ensures an authentication bearer token exists.
   * If not cached in the secure vault, registers an anonymous user with the backend.
   */
  async bootstrapAuth(): Promise<string> {
    const existing = await this.tokenProvider.get()
    if (existing && existing.trim().length > 0) {
      return existing.trim()
    }
    if (!this.allowAnonymousRegistration) {
      throw new AuthError('Sign in is required')
    }

    try {
      const response = await this.fetchFn(`${this.baseUrl}/v1/auth/register`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
      })

      if (!response.ok) {
        let detail: unknown
        try {
          detail = await response.json()
        } catch {
          detail = await response.text()
        }
        throw new AuthError(`Registration failed with status ${response.status}`, detail)
      }

      const data = (await response.json()) as RegisterResponse
      if (!data.access_token) {
        throw new AuthError('Registration response missing access_token')
      }

      await this.tokenProvider.set(data.access_token)
      return data.access_token
    } catch (err) {
      if (err instanceof ApiError) throw err
      throw new NetworkError(`Failed to connect to authentication endpoint: ${String(err)}`, err)
    }
  }

  /**
   * Clears the current auth token from the secure vault.
   */
  async logout(): Promise<void> {
    const token = await this.tokenProvider.get()
    try {
      if (token) await this.authenticatedRequest('/v1/auth/logout', { method: 'POST', token })
    } finally {
      await this.tokenProvider.clear()
    }
  }

  /**
   * Creates a new chat session.
   */
  async createSession(data: SessionCreate = {}): Promise<SessionRead> {
    const token = await this.bootstrapAuth()
    const response = await this.authenticatedRequest('/v1/sessions', {
      method: 'POST',
      token,
      body: JSON.stringify(data),
    })

    return (await response.json()) as SessionRead
  }

  /**
   * Retrieves an existing session and message history.
   */
  async getSession(sessionId: string): Promise<SessionDetailRead> {
    const token = await this.bootstrapAuth()
    const response = await this.authenticatedRequest(`/v1/sessions/${sessionId}`, {
      method: 'GET',
      token,
    })

    return (await response.json()) as SessionDetailRead
  }

  /**
   * Sends a user message to the session.
   * Includes idempotency request_id and optional snapshot_id.
   */
  async sendMessage(
    sessionId: string,
    params: {
      text: string
      snapshotId?: string
      requestId?: string
    }
  ): Promise<{ message: MessageRead; isReplay: boolean }> {
    if (!params.text || params.text.trim().length === 0) {
      throw new ApiError('Message text cannot be empty', 400)
    }

    const token = await this.bootstrapAuth()
    const payload: MessageCreate = {
      request_id: params.requestId || this.generateRequestId(),
      text: params.text.trim(),
      ...(params.snapshotId ? { snapshot_id: params.snapshotId } : {}),
    }

    const response = await this.authenticatedRequest(`/v1/sessions/${sessionId}/messages`, {
      method: 'POST',
      token,
      body: JSON.stringify(payload),
    })

    const isReplay = response.headers.get('Idempotent-Replay') === 'true'
    const message = (await response.json()) as MessageRead
    return { message, isReplay }
  }

  /**
   * Ingests a captured product snapshot.
   */
  async createSnapshot(data: SnapshotCreate): Promise<SnapshotResponse> {
    const token = await this.bootstrapAuth()
    const response = await this.authenticatedRequest('/v1/snapshots', {
      method: 'POST',
      token,
      body: JSON.stringify(data),
    })

    return (await response.json()) as SnapshotResponse
  }

  /**
   * Performs an authenticated request with standardized error translation.
   */
  private async authenticatedRequest(
    path: string,
    options: {
      method: string
      token: string
      body?: BodyInit
      contentType?: string
      headers?: Record<string, string>
    }
  ): Promise<Response> {
    let response: Response
    try {
      response = await this.fetchFn(`${this.baseUrl}${path}`, {
        method: options.method,
        headers: {
          'Content-Type': options.contentType || 'application/json',
          Accept: 'application/json',
          Authorization: `Bearer ${options.token}`,
          ...options.headers,
        },
        body: options.body,
      })
    } catch (err) {
      throw new NetworkError(`Network connection error calling ${path}: ${String(err)}`, err)
    }

    if (!response.ok) {
      let detail: unknown
      try {
        detail = await response.json()
      } catch {
        try {
          detail = await response.text()
        } catch {
          detail = null
        }
      }

      if (response.status === 401) {
        // Clear cached token if server rejects it
        await this.tokenProvider.clear()
        throw new AuthError(`Authentication failed: HTTP ${response.status}`, detail)
      }
      if (response.status === 404) {
        throw new NotFoundError(`Endpoint or resource not found: ${path}`, detail)
      }
      if (response.status === 429) {
        throw new RateLimitError('Rate limit exceeded', detail)
      }
      throw new ApiError(`Request to ${path} failed with HTTP ${response.status}`, response.status, detail)
    }

    return response
  }

  // IDs and versions come from committed replies. Keep event/claim IDs stable on
  // retries; the client deliberately does not retry or renumber browser mutations.
  private runtimePath(conversationId: string, suffix = ''): string {
    return `/v1/ai/conversations/${encodeURIComponent(conversationId)}${suffix}`
  }

  private async runtimeJson<T>(path: string, method: string, body?: unknown): Promise<T> {
    const token = await this.bootstrapAuth()
    const response = await this.authenticatedRequest(path, {
      method, token, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    return await response.json() as T
  }

  async getAiCapabilities(): Promise<RuntimeCapabilities> {
    return this.runtimeJson('/v1/ai/capabilities', 'GET')
  }

  async startAiConversation(sessionId: string, language: 'en' | 'hi-Latn' = 'hi-Latn'): Promise<RuntimeReply> {
    return this.runtimeJson(`/v1/ai/sessions/${encodeURIComponent(sessionId)}/conversation`, 'POST', { language })
  }

  async getAiConversation(conversationId: string): Promise<RuntimeReply> {
    return this.runtimeJson(this.runtimePath(conversationId), 'GET')
  }

  async sendAiEvent(conversationId: string, event: RuntimeEvent): Promise<RuntimeReply> {
    return this.runtimeJson(this.runtimePath(conversationId, '/events'), 'POST', event)
  }

  async confirmAiAction(conversationId: string, confirmation: RuntimeConfirmation): Promise<RuntimeReply> {
    return this.runtimeJson(this.runtimePath(conversationId, '/confirmations'), 'POST', confirmation)
  }

  async getAiWork(conversationId: string): Promise<RuntimeWork[]> {
    return this.runtimeJson(this.runtimePath(conversationId, '/work'), 'GET')
  }

  async claimAiWork(conversationId: string, dispatchId: string, claim: {
    claim_id: string; expected_version: number; target: RuntimeTarget; origin: string
  }): Promise<ClaimedRuntimeWork> {
    return this.runtimeJson(this.runtimePath(conversationId, `/work/${encodeURIComponent(dispatchId)}/claim`), 'POST', claim)
  }

  async submitAiReceipt(conversationId: string, dispatchId: string, receipt: RuntimeReceipt): Promise<RuntimeReply> {
    return this.runtimeJson(this.runtimePath(conversationId, `/work/${encodeURIComponent(dispatchId)}/receipts`), 'POST', receipt)
  }

  async deleteAiConversation(conversationId: string): Promise<void> {
    const token = await this.bootstrapAuth()
    await this.authenticatedRequest(this.runtimePath(conversationId), { method: 'DELETE', token })
  }

  async sendAiAudio(conversationId: string, eventId: string, version: number, audio: Blob,
    context?: { target: RuntimeTarget; origin: string }): Promise<RuntimeReply> {
    if (audio.size === 0 || audio.size > 5_000_000) throw new ApiError('Audio size is invalid', 413)
    const type = audio.type.split(';')[0]
    if (!['audio/wav', 'audio/webm', 'audio/ogg', 'audio/mpeg'].includes(type)) {
      throw new ApiError('Audio type is unsupported', 415)
    }
    if (context && (!context.target.active || !context.target.window_focused)) {
      throw new ApiError('Audio context requires a foreground page', 409)
    }
    const query = context ? '?' + new URLSearchParams({ tab_id: String(context.target.tab_id),
      document_id: context.target.document_id, origin: context.origin }) : ''
    const token = await this.bootstrapAuth()
    const response = await this.authenticatedRequest(this.runtimePath(conversationId, '/audio' + query), {
      method: 'POST', token, body: audio, contentType: type,
      headers: { 'X-Event-ID': eventId, 'X-Expected-Version': String(version) },
    })
    return await response.json() as RuntimeReply
  }

  async synthesizeAiSpeech(conversationId: string, version: number): Promise<{ audio: Blob; version: number }> {
    const token = await this.bootstrapAuth()
    const response = await this.authenticatedRequest(this.runtimePath(conversationId, '/speech'), {
      method: 'POST', token, body: JSON.stringify({ expected_version: version }),
    })
    if (response.headers.get('X-Conversation-Version') !== String(version)) {
      throw new ApiError('Speech version does not match the current reply', 409)
    }
    return { audio: await response.blob(), version }
  }

  private generateRequestId(): string {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID()
    }
    return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  }
}

export const apiClient = new ShoppingApiClient()
