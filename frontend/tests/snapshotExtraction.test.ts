import { test, describe } from 'node:test'
import assert from 'node:assert'
import {
  extractSnapshotFromDocument,
  type MinimalDocument,
} from '../src/utils/snapshotExtractor.ts'

describe('Browser Product Snapshot Extraction Unit Tests', () => {
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
