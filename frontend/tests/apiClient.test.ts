import { test, describe } from 'node:test'
import assert from 'node:assert'
import {
  ShoppingApiClient,
  AuthError,
  NotFoundError,
  RateLimitError,
  NetworkError,
  ApiError,
  desktopFetch,
  type TokenProvider,
} from '../src/services/apiClient.ts'

describe('ShoppingApiClient Integration Unit Tests', () => {
  test('packaged desktop JSON uses the trusted HTTP bridge and preserves 204 responses', async () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'window')
    const calls: unknown[] = []
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { electronAPI: {
      requestBackend: async (data: unknown) => { calls.push(data); return { status: 204, headers: {}, body: '' } },
    } } })
    try {
      const response = await desktopFetch('http://localhost:8000/v1/auth/logout', { method: 'POST', headers: { Authorization: 'Bearer token' } })
      assert.strictEqual(response.status, 204)
      assert.strictEqual(await response.text(), '')
      assert.strictEqual(calls.length, 1)
    } finally {
      if (original) Object.defineProperty(globalThis, 'window', original)
      else Reflect.deleteProperty(globalThis, 'window')
    }
  })
  function createMockTokenProvider(initialToken: string | null = null): TokenProvider & { tokens: string[] } {
    let currentToken = initialToken
    const tokens: string[] = []
    return {
      tokens,
      get: async () => currentToken,
      set: async (t: string) => {
        currentToken = t
        tokens.push(t)
      },
      clear: async () => {
        currentToken = null
      },
    }
  }

  test('bootstrapAuth retrieves token from /v1/auth/register when none is stored', async () => {
    const tokenProvider = createMockTokenProvider()
    let registerCalled = false

    const mockFetch: typeof fetch = async (input, init) => {
      const url = String(input)
      if (url.endsWith('/v1/auth/register')) {
        registerCalled = true
        assert.strictEqual(init?.method, 'POST')
        return new Response(
          JSON.stringify({
            access_token: 'test-bearer-token-123',
            token_type: 'bearer',
            expires_at: '2026-12-31T23:59:59Z',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } }
        )
      }
      throw new Error(`Unexpected URL: ${url}`)
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    const token = await client.bootstrapAuth()
    assert.strictEqual(token, 'test-bearer-token-123')
    assert.strictEqual(registerCalled, true)
    assert.strictEqual(await tokenProvider.get(), 'test-bearer-token-123')

    // Second call should reuse cached token and NOT call register again
    registerCalled = false
    const token2 = await client.bootstrapAuth()
    assert.strictEqual(token2, 'test-bearer-token-123')
    assert.strictEqual(registerCalled, false)
  })

  test('createSession posts to /v1/sessions with Authorization header', async () => {
    const tokenProvider = createMockTokenProvider('existing-token-abc')
    let calledHeaders: Headers | undefined
    let calledBody: string | undefined

    const mockFetch: typeof fetch = async (input, init) => {
      const url = String(input)
      if (url.endsWith('/v1/sessions')) {
        calledHeaders = new Headers(init?.headers)
        calledBody = String(init?.body)
        return new Response(
          JSON.stringify({
            id: 'session-uuid-456',
            title: 'Shopping for shoes',
            created_at: '2026-10-22T12:00:00Z',
            updated_at: '2026-10-22T12:00:00Z',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } }
        )
      }
      throw new Error(`Unexpected URL: ${url}`)
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    const session = await client.createSession({ title: 'Shopping for shoes' })
    assert.strictEqual(session.id, 'session-uuid-456')
    assert.strictEqual(session.title, 'Shopping for shoes')
    assert.strictEqual(calledHeaders?.get('Authorization'), 'Bearer existing-token-abc')
    assert.ok(calledBody?.includes('Shopping for shoes'))
  })

  test('sendMessage posts message and parses Idempotent-Replay header', async () => {
    const tokenProvider = createMockTokenProvider('auth-token-xyz')
    let requestedUrl = ''

    const mockFetch: typeof fetch = async (input, init) => {
      requestedUrl = String(input)
      const body = JSON.parse(String(init?.body))
      assert.strictEqual(body.text, 'Is this running shoe on sale?')
      assert.strictEqual(body.snapshot_id, 'snap-789')
      assert.ok(body.request_id.length > 0)

      return new Response(
        JSON.stringify({
          id: 'msg-101',
          session_id: 'session-uuid-456',
          role: 'assistant',
          content: 'Yes, it is discounted by 15%.',
          kind: 'answer',
          created_at: '2026-10-22T12:01:00Z',
        }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Idempotent-Replay': 'true',
          },
        }
      )
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    const result = await client.sendMessage('session-uuid-456', {
      text: 'Is this running shoe on sale?',
      snapshotId: 'snap-789',
    })

    assert.ok(requestedUrl.endsWith('/v1/sessions/session-uuid-456/messages'))
    assert.strictEqual(result.message.id, 'msg-101')
    assert.strictEqual(result.message.content, 'Yes, it is discounted by 15%.')
    assert.strictEqual(result.isReplay, true)
  })

  test('sendMessage rejects empty message before sending', async () => {
    const client = new ShoppingApiClient()
    await assert.rejects(
      async () => {
        await client.sendMessage('session-1', { text: '   ' })
      },
      (err) => err instanceof ApiError && err.statusCode === 400
    )
  })

  test('createSnapshot ingests product snapshot and parses response', async () => {
    const tokenProvider = createMockTokenProvider('auth-token-xyz')

    const mockFetch: typeof fetch = async (input, init) => {
      const body = JSON.parse(String(init?.body))
      assert.strictEqual(body.retailer, 'amazon_in')
      assert.strictEqual(body.retailer_product_id, 'B09V3KXJPB')
      assert.strictEqual(body.price, 4999.0)

      return new Response(
        JSON.stringify({
          snapshot_id: 'snapshot-uuid-999',
          listing_id: 'listing-uuid-888',
          price_recorded: true,
          price_summary: {
            verdict: 'good_deal',
            lowest_price: 4999.0,
            observation_count: 5,
          },
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      )
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    const response = await client.createSnapshot({
      retailer: 'amazon_in',
      retailer_product_id: 'B09V3KXJPB',
      title: 'Sony Wireless Headphones',
      price: 4999.0,
      availability: 'in_stock',
    })

    assert.strictEqual(response.snapshot_id, 'snapshot-uuid-999')
    assert.strictEqual(response.listing_id, 'listing-uuid-888')
    assert.strictEqual(response.price_recorded, true)
    assert.strictEqual(response.price_summary.verdict, 'good_deal')
  })

  test('authentication failure (401) throws AuthError and clears cached token', async () => {
    const tokenProvider = createMockTokenProvider('expired-token')

    const mockFetch: typeof fetch = async () => {
      return new Response(JSON.stringify({ detail: 'Token has expired' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    await assert.rejects(
      async () => {
        await client.createSession()
      },
      (err) => err instanceof AuthError && err.statusCode === 401
    )

    // Token must have been cleared from provider
    assert.strictEqual(await tokenProvider.get(), null)
  })

  test('not found error (404) throws NotFoundError', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')

    const mockFetch: typeof fetch = async () => {
      return new Response(JSON.stringify({ detail: 'Session not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    await assert.rejects(
      async () => {
        await client.getSession('non-existent-session')
      },
      (err) => err instanceof NotFoundError && err.statusCode === 404
    )
  })

  test('rate limit error (429) throws RateLimitError', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')

    const mockFetch: typeof fetch = async () => {
      return new Response(
        JSON.stringify({
          detail: { code: 'RATE_LIMITED', message: 'Daily limit exceeded' },
        }),
        { status: 429, headers: { 'Content-Type': 'application/json' } }
      )
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    await assert.rejects(
      async () => {
        await client.sendMessage('session-1', { text: 'Hello' })
      },
      (err) => err instanceof RateLimitError && err.statusCode === 429
    )
  })

  test('network failure throws NetworkError', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')

    const mockFetch: typeof fetch = async () => {
      throw new TypeError('Failed to fetch: Connection refused')
    }

    const client = new ShoppingApiClient({
      baseUrl: 'http://test-backend:8000',
      customFetch: mockFetch,
      tokenProvider,
    })

    await assert.rejects(
      async () => {
        await client.createSession()
      },
      (err) => err instanceof NetworkError && !err.message.includes('Connection refused') && err.originalError instanceof TypeError
    )
  })

  test('AI event IDs and concurrency preconditions are preserved without automatic retries', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')
    const calls: { path: string; body: unknown }[] = []
    const client = new ShoppingApiClient({ baseUrl: 'http://test-backend:8000', tokenProvider,
      customFetch: async (input, init) => {
        calls.push({ path: String(input), body: JSON.parse(String(init?.body)) })
        return new Response(JSON.stringify({ error: { code: 'VERSION_CONFLICT' } }), { status: 409 })
      },
    })
    const event = { event_id: 'stable-event', expected_version: 3, kind: 'cancel' as const }
    await assert.rejects(client.sendAiEvent('conversation-1', event), ApiError)
    assert.deepStrictEqual(calls, [{ path: 'http://test-backend:8000/v1/ai/conversations/conversation-1/events', body: event }])
  })

  test('unsupported site 403 preserves the valid identity token', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')
    const client = new ShoppingApiClient({ tokenProvider, customFetch: async () =>
      new Response(JSON.stringify({ error: { code: 'UNSUPPORTED_SITE' } }), { status: 403 }) })
    await assert.rejects(client.getAiWork('owned-conversation'), ApiError)
    assert.strictEqual(await tokenProvider.get(), 'valid-token')
  })

  test('logout revokes server token before clearing the local vault', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')
    let revoked = false
    const client = new ShoppingApiClient({ tokenProvider, customFetch: async (input, init) => {
      assert.ok(String(input).endsWith('/v1/auth/logout'))
      assert.strictEqual(init?.method, 'POST')
      assert.strictEqual(await tokenProvider.get(), 'valid-token')
      revoked = true
      return new Response(null, { status: 204 })
    } })
    await client.logout()
    assert.ok(revoked)
    assert.strictEqual(await tokenProvider.get(), null)
  })

  test('audio sends bounded raw media with stable event/version metadata', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')
    const clip = new Blob(['synthetic'], { type: 'audio/webm' })
    const client = new ShoppingApiClient({ tokenProvider, customFetch: async (input, init) => {
      assert.ok(String(input).endsWith('/audio'))
      assert.strictEqual(init?.body, clip)
      const headers = new Headers(init?.headers)
      assert.strictEqual(headers.get('Content-Type'), 'audio/webm')
      assert.strictEqual(headers.get('X-Event-ID'), 'voice-event')
      assert.strictEqual(headers.get('X-Expected-Version'), '4')
      return new Response(JSON.stringify({ version: 5 }), { status: 200 })
    } })
    await client.sendAiAudio('conversation-1', 'voice-event', 4, clip)
    await assert.rejects(client.sendAiAudio('conversation-1', 'invalid', 4, new Blob()), ApiError)
  })

  test('account mode requires sign-in and does not bootstrap an anonymous identity', async () => {
    const tokenProvider = createMockTokenProvider()
    const client = new ShoppingApiClient({ tokenProvider, allowAnonymousRegistration: false,
      customFetch: async () => { throw new Error('Registration must not be attempted') } })
    await assert.rejects(client.bootstrapAuth(), AuthError)
    await tokenProvider.set('verified-access-token')
    assert.strictEqual(await client.bootstrapAuth(), 'verified-access-token')
  })

  test('capabilities are read with the current account token', async () => {
    const tokenProvider = createMockTokenProvider('verified-access-token')
    const client = new ShoppingApiClient({ tokenProvider, allowAnonymousRegistration: false,
      customFetch: async (input, init) => {
        assert.ok(String(input).endsWith('/v1/ai/capabilities'))
        assert.strictEqual(new Headers(init?.headers).get('Authorization'), 'Bearer verified-access-token')
        return Response.json({ authentication: 'oidc', provider: 'mock' })
      } })
    assert.strictEqual((await client.getAiCapabilities()).authentication, 'oidc')
  })

  test('speech result rejects obsolete playback versions', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')
    const client = new ShoppingApiClient({ tokenProvider, customFetch: async () =>
      new Response('audio', { status: 200, headers: { 'X-Conversation-Version': '8' } }) })
    await assert.rejects(client.synthesizeAiSpeech('conversation-1', 7), ApiError)
  })

  test('shopping analysis submits typed observations without action retries', async () => {
    const tokenProvider = createMockTokenProvider('valid-token')
    let calls = 0
    const client = new ShoppingApiClient({ tokenProvider, customFetch: async (input, init) => {
      calls++
      assert.ok(String(input).endsWith('/v1/ai/analysis'))
      const body = JSON.parse(String(init?.body))
      assert.strictEqual(body.question, 'battery?')
      assert.strictEqual(body.budget, '2000')
      return Response.json({ status: 'no_evidence', answer: 'Capture a product' })
    } })
    assert.strictEqual((await client.analyzeShopping('battery?', [], { budget: '2000' })).status, 'no_evidence')
    assert.strictEqual(calls, 1)
    await assert.rejects(client.analyzeShopping('', []), ApiError)
    assert.strictEqual(calls, 1)
  })
})
