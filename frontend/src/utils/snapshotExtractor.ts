/**
 * Safe Browser Snapshot Extractor.
 * Extracts structured product information (JSON-LD, OpenGraph, DOM fallbacks)
 * from the active browser page without exposing privileged Electron APIs.
 */

import type { AnalysisProduct, ShoppingAnalysis } from '../types/assistant'

export function isSupportedProductAddress(address: string): boolean {
  try {
    const url = new URL(address)
    return url.protocol === 'https:' && !url.username && !url.password &&
      ['amazon.in', 'www.amazon.in', 'flipkart.com', 'www.flipkart.com'].includes(url.hostname) &&
      (/\/(?:dp|gp\/product|ASIN)\/[a-z0-9]{10}(?:\/|$)/i.test(url.pathname) || /\/p\/itm[a-z0-9]+(?:\/|$)/i.test(url.pathname))
  } catch { return false }
}

/** User-initiated local diagnostics: never include URLs, queries, IDs or page text. */
export function buildReadDiagnostic(sourceUrl: string, issue: 'read_failed' | 'check_page' | 'service_unavailable'): string {
  let site = 'unsupported'
  try {
    const url = new URL(sourceUrl)
    if (url.protocol === 'https:' && ['amazon.in', 'www.amazon.in'].includes(url.hostname)) site = 'amazon_in'
    if (url.protocol === 'https:' && ['flipkart.com', 'www.flipkart.com'].includes(url.hostname)) site = 'flipkart'
  } catch { /* Reports intentionally omit unrecognized addresses. */ }
  return JSON.stringify({ report_version: 1, adapter_version: 'captured-dom-1', platform: 'electron_desktop', site, issue }, null, 2)
}

export function rawCaptureFallback(capture: AnalysisProduct, now = Date.now()): ShoppingAnalysis {
  const p = capture.product
  const result: ShoppingAnalysis = {
    status: 'needs_input', processing: 'local_capture_fallback',
    answer: 'The shopping service is unavailable. These are captured page details; your question has not been analyzed.',
    evidence: [], comparison: [], shortlist: [], lowest_observed_price_product_id: null,
    review_sample_size: 0, review_match_count: 0,
    coverage: 'Current page capture only. No recommendation, review consensus or checkout action.',
    warnings: ['LOCAL_CAPTURE_FALLBACK'], follow_up: 'Retry your question when the service is available.', skills: [],
  }
  let source: URL
  try { source = new URL(capture.source_url) } catch { return result }
  const age = now - Date.parse(p.observed_at)
  if (source.protocol !== 'https:' || source.username || source.password ||
      !['www.amazon.in', 'amazon.in', 'www.flipkart.com', 'flipkart.com'].includes(source.hostname) ||
      !Number.isFinite(age) || age < 0 || age > 120000) return result
  const facts: [string, string, string][] = [['title', 'title', p.title]]
  if (p.price !== null && p.currency) facts.push(['price', 'price', `${p.currency} ${p.price}`])
  if (p.in_stock !== null) facts.push(['stock', 'stock', p.in_stock ? 'in stock' : 'out of stock'])
  for (const [key, value] of Object.entries(p.specs).slice(0, 30)) facts.push(['spec', key, value])
  result.evidence = facts.map(([kind, label, value], index) => ({ evidence_id: `raw-${index}`,
    product_id: p.product_id, product_title: p.title, snapshot_id: p.snapshot_id, document_id: p.document_id,
    source_url: capture.source_url, observed_at: p.observed_at, kind, label, value,
    review_id: null, rating: null, verified_purchase_observed: null }))
  return result
}

export interface ExtractedSnapshot {
  retailer: string
  retailer_product_id: string
  title: string
  url: string
  price: string
  currency: string
  availability: string
  captured_at: string
}

export interface ExtractionResult {
  success: boolean
  snapshot?: ExtractedSnapshot
  error?: string
}

export interface MinimalDocument {
  title?: string
  querySelectorAll?: (selector: string) => Array<{
    textContent?: string | null
    getAttribute?: (name: string) => string | null
    closest?: (selector: string) => unknown
  }>
  querySelector?: (selector: string) => {
    textContent?: string | null
    getAttribute?: (name: string) => string | null
  } | null
}

export function extractSnapshotFromDocument(
  doc: MinimalDocument,
  pageUrl: string,
  nowIsoString: string = new Date().toISOString()
): ExtractionResult {
  try {
    let hostname = ''
    let pathname = ''
    try {
      const parsedUrl = new URL(pageUrl)
      hostname = parsedUrl.hostname.toLowerCase()
      pathname = parsedUrl.pathname
    } catch {
      hostname = ''
      pathname = pageUrl
    }

    let retailer = 'unknown'
    if (hostname.includes('amazon')) {
      retailer = 'amazon_in'
    } else if (hostname.includes('flipkart')) {
      retailer = 'flipkart'
    }

    let retailerProductId = ''
    const asinMatch = pageUrl.match(/(?:\/dp\/|\/gp\/product\/|\/ASIN\/)([A-Z0-9]{10})/i)
    if (asinMatch) {
      retailerProductId = asinMatch[1]
    } else {
      const flipkartMatch = pageUrl.match(/\/p\/(itm[a-zA-Z0-9]+)/i)
      if (flipkartMatch) {
        retailerProductId = flipkartMatch[1]
      } else {
        retailerProductId = pathname.replace(/[^a-zA-Z0-9_-]/g, '_').slice(-30) || 'unknown_item'
      }
    }

    let price: string | null = null
    let currency = 'INR'
    let inStock = true

    // 1. Try structured JSON-LD (<script type="application/ld+json">)
    if (doc.querySelectorAll) {
      const scripts = doc.querySelectorAll('script[type="application/ld+json"]')
      for (const s of scripts) {
        try {
          const content = s.textContent || ''
          const parsed = JSON.parse(content)
          const items = Array.isArray(parsed) ? parsed : [parsed]
          for (const item of items) {
            if (
              item &&
              (item['@type'] === 'Product' ||
                item['@type'] === 'http://schema.org/Product' ||
                item['@type'] === 'https://schema.org/Product')
            ) {
              const offer = Array.isArray(item.offers) ? item.offers[0] : item.offers
              if (offer) {
                if (offer.price !== undefined && offer.price !== null) {
                  price = String(offer.price).replace(/[^0-9.]/g, '')
                }
                if (offer.priceCurrency) {
                  currency = String(offer.priceCurrency).toUpperCase()
                }
                if (offer.availability) {
                  inStock = String(offer.availability).toLowerCase().includes('instock')
                }
              }
              break
            }
          }
        } catch {
          // Ignore malformed JSON-LD scripts safely
        }
        if (price) break
      }
    }

    // 2. Fallback to OpenGraph / Product Meta Tags
    if (!price && doc.querySelector) {
      const priceMeta = doc.querySelector(
        'meta[property="product:price:amount"], meta[property="og:price:amount"]'
      )
      if (priceMeta && priceMeta.getAttribute) {
        const rawContent = priceMeta.getAttribute('content')
        if (rawContent) price = rawContent.replace(/[^0-9.]/g, '') || null
      }
      const currMeta = doc.querySelector(
        'meta[property="product:price:currency"], meta[property="og:price:currency"]'
      )
      if (currMeta && currMeta.getAttribute) {
        const rawCurrency = currMeta.getAttribute('content')
        if (rawCurrency) currency = rawCurrency.toUpperCase()
      }
    }

    // 3. Fallback to DOM elements for known shopping sites
    if (!price && retailer === 'amazon_in' && doc.querySelector) {
      const amazonElem = doc.querySelector(
        '.a-price .a-offscreen, #priceblock_ourprice, #priceblock_dealprice'
      )
      if (amazonElem && amazonElem.textContent) {
        price = amazonElem.textContent.replace(/[^0-9.]/g, '') || null
      }
    }

    const rawTitle = (doc.title || '').trim()

    return {
      success: true,
      snapshot: {
        retailer,
        retailer_product_id: retailerProductId,
        title: rawTitle.slice(0, 500) || 'Untitled Page',
        url: new URL(pageUrl).origin + new URL(pageUrl).pathname,
        price: price || '0.00',
        currency,
        availability: inStock ? 'in_stock' : 'out_of_stock',
        captured_at: nowIsoString,
      },
    }
  } catch (error) {
    return {
      success: false,
      error: String(error),
    }
  }
}
