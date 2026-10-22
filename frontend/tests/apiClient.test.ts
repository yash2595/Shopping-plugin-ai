import { test, describe } from 'node:test'
import assert from 'node:assert'
import {
  ShoppingApiClient,
  AuthError,
  NotFoundError,
  RateLimitError,
  NetworkError,
  ApiError,
  type TokenProvider,
} from '../src/services/apiClient.ts'

describe('ShoppingApiClient Integration Unit Tests', () => {
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
      (err) => err instanceof NetworkError && err.message.includes('Connection refused')
    )
  })
})
