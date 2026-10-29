/** Runs without privileges in the retailer document. No requested identity is input. */
export function extractStructuredOffers(doc: Pick<Document, 'querySelectorAll'>, address: string) {
  const source = new URL(address)
  const offers: Record<string, unknown>[] = []
  if (source.protocol !== 'https:' || source.username || source.password || source.port ||
      !['www.amazon.in', 'amazon.in', 'www.flipkart.com', 'flipkart.com'].includes(source.hostname)) return offers
  const text = (value: unknown) => typeof value === 'string' && value.trim().length > 0 && value.trim().length <= 200 && !Array.from(value).some((c) => c.charCodeAt(0) < 32) ? value.trim() : null
  const money = (value: unknown) => (typeof value === 'string' || typeof value === 'number') && /^(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$/.test(String(value)) ? String(value) : null
  const name = (value: unknown): string | null => text(value) || (value && typeof value === 'object' ? text((value as Record<string, unknown>).name) : null)
  const schema = (value: unknown, label: string) => value === label || value === `https://schema.org/${label}` || value === `http://schema.org/${label}`
  const seen = new Set<string>()
  let nodes = 0
  const walk = (value: unknown, depth: number) => {
    if (++nodes > 400 || depth > 6 || offers.length >= 20 || !value || typeof value !== 'object') return
    if (Array.isArray(value)) { for (const item of value.slice(0, 100)) walk(item, depth + 1); return }
    const product = value as Record<string, unknown>
    if (schema(product['@type'], 'Product')) {
      const raw = Array.isArray(product.offers) ? product.offers.length === 1 ? product.offers[0] : null : product.offers
      if (!raw || typeof raw !== 'object') return
      const offer = raw as Record<string, unknown>
      if (!schema(offer['@type'], 'Offer')) return
      const brand = name(product.brand), model = text(product.model), seller = name(offer.seller)
      const condition = ['New', 'Used', 'Refurbished'].find((kind) => schema(offer.itemCondition, `${kind}Condition`))?.toLowerCase()
      const price = money(offer.price), currency = text(offer.priceCurrency)
      if (!brand || !model || !seller || !condition || !price || Number(price) <= 0 || !currency || !/^[A-Z]{3}$/.test(currency)) return
      const attributes: Record<string, string> = Object.create(null)
      for (const field of ['color', 'size']) { const v = text(product[field]); if (v) attributes[field === 'color' ? 'colour' : field] = v }
      for (const entry of (Array.isArray(product.additionalProperty) ? product.additionalProperty : []).slice(0, 20)) {
        if (!entry || typeof entry !== 'object') continue
        const key = text(entry.name), v = text(entry.value)
        if (!key || !v) continue
        const normalized = key.toLowerCase().replace(/\s+/g, ' ')
        if (Object.hasOwn(attributes, normalized) && attributes[normalized] !== v) return
        attributes[normalized] = v
      }
      if (!Object.keys(attributes).length || Object.keys(attributes).length > 20) return
      try {
        const url = new URL(String(offer.url || product.url || ''), source)
        if (!offer.url && !product.url || url.origin !== source.origin || url.username || url.password ||
            !(/\/(?:dp|gp\/product)\/[a-z0-9]{10}(?:\/|$)/i.test(url.pathname) || /\/p\/itm[a-z0-9]+(?:\/|$)/i.test(url.pathname))) return
        url.search = ''; url.hash = ''
        if (seen.has(url.href)) return
        seen.add(url.href)
        offers.push({ identity: { brand, model, condition, attributes }, url: url.href, seller,
          price, currency, in_stock: schema(offer.availability, 'InStock') ? true : schema(offer.availability, 'OutOfStock') ? false : null,
          // Search snippets do not establish delivery-context charges or final taxes.
          shipping: null, extra_tax_and_fees: null, conditional_discount_note: null })
      } catch { /* Ignore malformed retailer data. */ }
      return
    }
    for (const key of ['@graph', 'itemListElement', 'item']) if (product[key]) walk(product[key], depth + 1)
  }
  for (const script of Array.from(doc.querySelectorAll('script[type="application/ld+json"]')).slice(0, 20)) {
    const body = script.textContent || ''
    if (body.length > 100000) continue
    try { walk(JSON.parse(body), 0) } catch { /* Non-JSON content is never executed. */ }
  }
  return offers
}
