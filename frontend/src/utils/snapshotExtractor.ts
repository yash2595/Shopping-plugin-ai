/**
 * Safe Browser Snapshot Extractor.
 * Extracts structured product information (JSON-LD, OpenGraph, DOM fallbacks)
 * from the active browser page without exposing privileged Electron APIs.
 */

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
        url: pageUrl,
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
