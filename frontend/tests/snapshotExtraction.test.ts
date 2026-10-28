import { test, describe } from 'node:test'
import assert from 'node:assert'
import { readFileSync } from 'node:fs'
import { transformSync } from 'esbuild'
import {
  extractSnapshotFromDocument,
  rawCaptureFallback,
  type MinimalDocument,
} from '../src/utils/snapshotExtractor.ts'

describe('Browser Product Snapshot Extraction Unit Tests', () => {
  test('native HTTP bridge rejects foreign origins/frames and never forwards retailer cookies', async () => {
    const source = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8')
    const start = source.indexOf("ipcMain.handle('backend:request'")
    const end = source.indexOf('// Safe Snapshot Extraction', start)
    assert.ok(start > 0 && end > start)
    const code = transformSync(source.slice(start, end), { loader: 'ts' }).code
    let bridge: (event: unknown, data: unknown) => Promise<{ status: number; body: string }>
    const calls: { url: string; options: RequestInit }[] = []
    const nativeFetch = async (url: URL, options: RequestInit) => {
      calls.push({ url: String(url), options })
      return Response.json({ status: 'ok' })
    }
    new Function('ipcMain', 'assertTrustedUi', 'secureAuthToken', 'process', 'fetch', 'Buffer', code)(
      { handle: (_name: string, handler: typeof bridge) => { bridge = handler } },
      (event: string) => { if (event !== 'trusted') throw new Error('Untrusted frame') },
      'native-vault-token', { env: {} }, nativeFetch, Buffer,
    )
    const input = { url: 'http://localhost:8000/v1/ai/analysis', method: 'POST', headers: {}, body: '{}' }
    await assert.rejects(() => bridge!('foreign', input), /Untrusted frame/)
    assert.strictEqual((await bridge!('trusted', { ...input, url: 'https://attacker.test/v1/ai/analysis' })).status, 503)
    assert.strictEqual((await bridge!('trusted', { ...input, body: 'x'.repeat(262145) })).status, 503)
    assert.strictEqual(calls.length, 0)
    const result = await bridge!('trusted', { ...input, headers: { Cookie: 'retailer-session=private', Authorization: 'Bearer injected' } })
    assert.strictEqual(result.status, 200)
    assert.strictEqual(calls.length, 1)
    const headers = calls[0].options.headers as Record<string, string>
    assert.strictEqual(headers.Authorization, 'Bearer native-vault-token')
    assert.ok(!('Cookie' in headers))
    assert.strictEqual(calls[0].options.redirect, 'error')
    assert.strictEqual(calls[0].options.credentials, 'omit')
  })
  test('offline fallback displays fresh raw facts without inventing stock or a recommendation', () => {
    const time = '2026-10-28T10:00:00Z'
    const capture = { source_url: 'https://www.amazon.in/dp/B09V3KXJPB', reviews: [], product: {
      product_id: 'a', snapshot_id: 's', document_id: 'd', title: 'Observed phone', observed_at: time,
      price: null, currency: null, in_stock: null, selected_variant_id: null, variants: [], specs: { Battery: '5000 mAh' },
    } }
    const result = rawCaptureFallback(capture, Date.parse(time))
    assert.strictEqual(result.processing, 'local_capture_fallback')
    assert.strictEqual(result.shortlist.length, 0)
    assert.ok(result.answer.includes('has not been analyzed'))
    assert.ok(result.evidence.some((e) => e.value === '5000 mAh'))
    assert.ok(!result.evidence.some((e) => e.kind === 'stock' || e.kind === 'price'))
    assert.strictEqual(rawCaptureFallback(capture, Date.parse(time) + 121000).evidence.length, 0)
    assert.strictEqual(rawCaptureFallback({ ...capture, source_url: 'https://attacker.test/p' }, Date.parse(time)).evidence.length, 0)
  })
  test('packaged highlighting requires exact visible evidence and treats keys as data', () => {
    const source = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8')
    const raw = source.match(/return await view\.webContents\.executeJavaScript\(`([\s\S]*?)`, true\)/)
    assert.ok(raw)
    let scrolls = 0
    const row = { querySelectorAll: () => [{ textContent: 'Battery' }, { textContent: '5000 mAh' }],
      getClientRects: () => [1], scrollIntoView: () => { scrolls++ }, style: { outline: '' } }
    const document = { querySelectorAll: () => [row] }
    for (const [key, expected, success] of [
      ['Battery', '5000 mAh', true], ['Battery', '6000 mAh', false],
      ["Battery'); throw new Error('injected')", '5000 mAh', false],
    ] as const) {
      const args = { skill: 'highlight_spec', spec_key: key, expected_text: expected }
      const script = new Function('args', 'return `' + raw[1] + '`')(args)
      const result = new Function('document', 'setTimeout', 'return (' + script + ')')(document, (callback: () => void) => callback())
      assert.strictEqual(result.success, success)
    }
    assert.strictEqual(scrolls, 1)
  })
  test('packaged native extraction captures exact specs and reviews without inventing stock', () => {
    const source = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8')
    const raw = source.match(/const rawData = await targetView\.webContents\.executeJavaScript\(`([\s\S]*?)`, true\)/)
    assert.ok(raw)
    const script = new Function('return `' + raw[1] + '`')()
    let reviewText = 'Heats up during calls. Exact punctuation!'
    let ratingText = '1.0 out of 5'
    const document = {
      title: 'Synthetic phone',
      querySelector: () => null,
      querySelectorAll: (selector: string) => {
        if (selector.includes('techSpec_section')) return [{ querySelectorAll: () => [{ textContent: 'Battery' }, { textContent: '5000 mAh' }] }]
        if (selector.includes('data-hook="review"')) return [{ querySelector: (value: string) =>
          value.includes('review-body') ? { textContent: reviewText } : value.includes('review-star-rating') ? { textContent: ratingText } : null }]
        return []
      },
    }
    const result = new Function('document', 'window', 'return (' + script + ')')(document, {
      location: { href: 'https://www.amazon.in/dp/B09V3KXJPB', hostname: 'www.amazon.in', pathname: '/dp/B09V3KXJPB' },
    })
    assert.strictEqual(result.success, true)
    assert.strictEqual(result.snapshot.availability, 'unknown')
    assert.strictEqual(result.snapshot.specs.Battery, '5000 mAh')
    assert.strictEqual(result.snapshot.reviews[0].text, reviewText)
    assert.strictEqual(result.snapshot.reviews[0].rating, 1)
    assert.strictEqual(result.snapshot.reviews[0].verified_purchase_observed, null)
    for (const malformed of ['4.5 stars', '10 stars']) {
      ratingText = malformed
      const rejected = new Function('document', 'window', 'return (' + script + ')')(document, {
        location: { href: 'https://www.amazon.in/dp/B09V3KXJPB', hostname: 'www.amazon.in', pathname: '/dp/B09V3KXJPB' },
      })
      assert.strictEqual(rejected.snapshot.reviews.length, 0)
    }
    ratingText = '1.0 out of 5'
    reviewText = '😀'.repeat(1001)
    const unicode = new Function('document', 'window', 'return (' + script + ')')(document, {
      location: { href: 'https://www.amazon.in/dp/B09V3KXJPB', hostname: 'www.amazon.in', pathname: '/dp/B09V3KXJPB' },
    })
    assert.strictEqual(Array.from(unicode.snapshot.reviews[0].text).length, 1000)
    for (const [offers, price, stock] of [
      [{ price: '1e3', priceCurrency: 'INR', availability: 'https://schema.org/InStock' }, '0.00', 'in_stock'],
      [{ price: '1,299.00', priceCurrency: 'INR', availability: 'https://schema.org/NotInStock' }, '1299.00', 'unknown'],
      [[{ price: '100', priceCurrency: 'INR' }, { price: '200', priceCurrency: 'INR' }], '0.00', 'unknown'],
    ] as const) {
      const structured = { ...document, querySelectorAll: (selector: string) => selector === 'script[type="application/ld+json"]'
        ? [{ textContent: JSON.stringify({ '@type': 'Product', offers }) }] : document.querySelectorAll(selector) }
      const observed = new Function('document', 'window', 'return (' + script + ')')(structured, {
        location: { href: 'https://www.amazon.in/dp/B09V3KXJPB', hostname: 'www.amazon.in', pathname: '/dp/B09V3KXJPB' },
      })
      assert.strictEqual(observed.snapshot.price, price)
      assert.strictEqual(observed.snapshot.availability, stock)
    }
  })
  test('Page 1: Amazon product page with JSON-LD and ASIN', () => {
    const amazonUrl = 'https://www.amazon.in/dp/B09V3KXJPB?ref=cm_sw_r_cp_ud_dp_test'
    const amazonDoc: MinimalDocument = {
      title: 'Sony WH-1000XM5 Wireless Noise Cancelling Headphones - Amazon.in',
      querySelectorAll: (selector: string) => {
        if (selector === 'script[type="application/ld+json"]') {
          return [
            {
              textContent: JSON.stringify({
                '@context': 'https://schema.org/',
                '@type': 'Product',
                name: 'Sony WH-1000XM5',
                offers: {
                  '@type': 'Offer',
                  price: 26990.0,
                  priceCurrency: 'INR',
                  availability: 'https://schema.org/InStock',
                },
              }),
            },
          ]
        }
        return []
      },
      querySelector: () => null,
    }

    const result = extractSnapshotFromDocument(amazonDoc, amazonUrl, '2026-10-22T14:00:00.000Z')
    assert.strictEqual(result.success, true)
    assert.ok(result.snapshot)
    assert.strictEqual(result.snapshot?.retailer, 'amazon_in')
    assert.strictEqual(result.snapshot?.retailer_product_id, 'B09V3KXJPB')
    assert.strictEqual(result.snapshot?.price, '26990')
    assert.strictEqual(result.snapshot?.currency, 'INR')
    assert.strictEqual(result.snapshot?.availability, 'in_stock')
    assert.strictEqual(result.snapshot?.captured_at, '2026-10-22T14:00:00.000Z')
  })

  test('Page 2: Flipkart product page with itm ID and OpenGraph meta fallback', () => {
    const flipkartUrl = 'https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4?pid=MOBGTAGPTB3VS24W'
    const flipkartDoc: MinimalDocument = {
      title: 'Apple iPhone 15 (Black, 128 GB) Price in India - Flipkart',
      querySelectorAll: () => [],
      querySelector: (selector: string) => {
        if (selector.includes('product:price:amount') || selector.includes('og:price:amount')) {
          return {
            getAttribute: () => '69999.00',
          }
        }
        if (selector.includes('product:price:currency') || selector.includes('og:price:currency')) {
          return {
            getAttribute: () => 'INR',
          }
        }
        return null
      },
    }

    const result = extractSnapshotFromDocument(flipkartDoc, flipkartUrl, '2026-10-22T14:05:00.000Z')
    assert.strictEqual(result.success, true)
    assert.ok(result.snapshot)
    assert.strictEqual(result.snapshot?.retailer, 'flipkart')
    assert.strictEqual(result.snapshot?.retailer_product_id, 'itm6ac6485515ae4')
    assert.strictEqual(result.snapshot?.price, '69999.00')
    assert.strictEqual(result.snapshot?.currency, 'INR')
    assert.strictEqual(result.snapshot?.availability, 'in_stock')
  })

  test('Page 3: Non-product page (Google Search / News) safely handled', () => {
    const googleUrl = 'https://www.google.com/search?q=weather+today'
    const googleDoc: MinimalDocument = {
      title: 'weather today - Google Search',
      querySelectorAll: () => [],
      querySelector: () => null,
    }

    const result = extractSnapshotFromDocument(googleDoc, googleUrl, '2026-10-22T14:10:00.000Z')
    assert.strictEqual(result.success, true)
    assert.ok(result.snapshot)
    assert.strictEqual(result.snapshot?.retailer, 'unknown')
    assert.strictEqual(result.snapshot?.title, 'weather today - Google Search')
    assert.strictEqual(result.snapshot?.price, '0.00')
    assert.strictEqual(result.snapshot?.availability, 'in_stock')
  })

  test('Page with malformed JSON-LD recovers gracefully and does not throw', () => {
    const badDoc: MinimalDocument = {
      title: 'Corrupted Page',
      querySelectorAll: (selector: string) => {
        if (selector === 'script[type="application/ld+json"]') {
          return [
            { textContent: '{{{ INVALID JSON HERE }}}' },
            {
              textContent: JSON.stringify({
                '@type': 'Product',
                offers: { price: 1499, priceCurrency: 'USD' },
              }),
            },
          ]
        }
        return []
      },
      querySelector: () => null,
    }

    const result = extractSnapshotFromDocument(badDoc, 'https://example.com/product/123')
    assert.strictEqual(result.success, true)
    assert.strictEqual(result.snapshot?.price, '1499')
    assert.strictEqual(result.snapshot?.currency, 'USD')
  })
})
