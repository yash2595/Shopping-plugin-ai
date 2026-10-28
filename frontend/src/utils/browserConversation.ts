/** Only direct user utterances enter this planner. Never feed page/model text here. */
export type BrowserCommand = { kind: 'navigate'; url: string } | { kind: 'back' | 'forward' | 'reload' | 'scroll_down' | 'scroll_up' } | { kind: 'switch'; tabId: string } | { kind: 'open_result'; index: number }
export interface ConversationPlan { reply?: string; command?: BrowserCommand; pendingSearch?: string; analyze?: boolean }
export interface ShoppingPreferences { budget?: string; specs: Record<string, string> }
/** Explicit user preferences only; questions/negations must not silently set requirements. */
export function updateShoppingPreferences(question: string, previous: ShoppingPreferences): ShoppingPreferences {
  const next = { budget: previous.budget, specs: { ...previous.specs } }
  if (/\b(?:not|dont|don't|no|nahi|mat)\b|नहीं|मत/u.test(question.toLowerCase())) return next
  if (/\b(?:forget|clear|remove)\s+(?:my\s+)?budget\b/i.test(question)) next.budget = undefined
  const scales: Record<string, number> = { k: 1000, thousand: 1000, hazar: 1000, lakh: 100000, crore: 10000000 }
  const amounts = new Set<string>()
  for (const match of question.toLowerCase().matchAll(/(?:budget\s*(?:is|hai|:)?|under|below)\s*(?:rs\.?|inr|₹)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(k|thousand|hazar|lakh|crore)?(?![\w.])/g)) {
    if (!/^(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d{1,2})?$/.test(match[1])) return previous
    const amount = Number(match[1].replaceAll(',', '')) * (scales[match[2]] || 1)
    if (!Number.isFinite(amount) || amount <= 0 || amount >= 1e12) return previous
    amounts.add(amount.toFixed(2))
  }
  if (amounts.size > 1) return previous
  if (amounts.size === 1) next.budget = [...amounts][0]
  if (!/\b(?:need|want|required|must|chahiye|recommend|suggest|show|find|search|only|with)\b|चाहिए/i.test(question) &&
      !/^(?:ram|storage|rom|colour|color)\s*[:=]/i.test(question)) return next
  const slots = new Map<string, Set<string>>()
  for (const match of question.toLowerCase().matchAll(/(\d+\s*(?:gb|mb))\s*(ram|storage|rom)\b|\b(ram|storage|rom)\s*[:=]?\s*(\d+\s*(?:gb|mb))\b/g)) {
    const key = (match[2] || match[3]) === 'ram' ? 'RAM' : 'Storage'
    const values = slots.get(key) || new Set<string>()
    values.add((match[1] || match[4]).replace(/\s/g, '')); slots.set(key, values)
  }
  for (const [key, values] of slots) { if (values.size !== 1) return previous; next.specs[key] = [...values][0] }
  const colours = ['black', 'blue', 'white', 'red', 'green'].filter((colour) => new RegExp(`\\b${colour}\\b`, 'i').test(question))
  if (colours.length > 1) return previous
  if (colours.length === 1) next.specs.Colour = colours[0]
  return next
}
export function safeConversationUrl(raw: string): string | null {
  try {
    const value = raw.trim()
    if (value.length > 2000 || (/[\s\\]/u.test(value) || Array.from(value).some((char) => char.charCodeAt(0) < 32))) return null
    const url = new URL(value.includes('://') ? value : `https://${value}`)
    if (url.protocol !== 'https:' || url.username || url.password || url.port || !url.hostname.includes('.') ||
      /^(localhost|127\.|0\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/i.test(url.hostname) ||
      /\.(localhost|local|internal)$/i.test(url.hostname)) return null
    if (/\/(?:cart|checkout|payment|buy|order|logout|delete)(?:[/?-]|$)/i.test(url.pathname)) return null
    return url.href
  } catch { return null }
}
export function planConversation(text: string, tabs: { id: string; title: string; url: string }[], pendingSearch?: string, activeTabId = tabs[0]?.id): ConversationPlan {
  const q = text.trim().replace(/^(?:please|bhai|saarthi)[, ]+/i, '').replace(/\s+please[.!]?$/i, '')
    .replace(/अमेज़न|अमेजन/g, 'amazon').replace(/फ्लिपकार्ट/g, 'flipkart').replace(/गूगल/g, 'google')
  if (!q || q.length > 2000) return { reply: 'Please keep your request between 1 and 2000 characters.' }
  const sites: Record<string, string> = { amazon: 'https://www.amazon.in/', flipkart: 'https://www.flipkart.com/', google: 'https://www.google.com/' }
  const siteOnly = /^(amazon|flipkart|google)(?: par| pe)?[.!]?$/i.exec(q)
  if (pendingSearch && siteOnly) return search(pendingSearch, siteOnly[1].toLowerCase())
  if (pendingSearch && /^(amazon|flipkart|google)\s+पर$/i.test(q)) return search(pendingSearch, q.split(/\s/)[0].toLowerCase())
  if (/^(hi|hello|hey|namaste|namaskar|hii+|हाय|नमस्ते)[!. ]*$/i.test(q)) return { reply: 'Namaste! Main Saarthi hoon. Website khol sakta hoon, search aur tabs navigate kar sakta hoon, aur product specs/reviews compare kar sakta hoon. Kya dhoondhna hai?' }
  if (/^(help|what can you do|tum kya kar sakte ho|madad)[?.!]*$/i.test(q)) return { reply: 'Try “Amazon kholo”, “search headphones on Flipkart”, “back”, “next tab”, or ask about an open product. I use captured evidence for shopping answers. Buying and checkout are disabled.' }
  if (/^(thanks|thank you|shukriya|dhanyavad)[.!]*$/i.test(q)) return { reply: 'Welcome! Agla product dekhna hai ya inki comparison chahiye?' }
  if (/^(?:do not|don’t|dont|mat|nahi|नहीं|मत)\b/i.test(q) || /\b(?:mat|nahi)\s+(?:kholo|karo|jao)\b/i.test(q)) return { reply: 'Theek hai, navigation nahi karunga. Batao kya dekhna hai.' }
  if (/\b(?:and then|then|aur phir|phir|&&)\b|[;\n]/i.test(q)) return { reply: 'Please give one browser command at a time so I can verify its result.' }
  if (/^(?:go )?back$|^peeche(?: jao)?$|^pichhe(?: jao)?$|^पीछे(?: जाओ)?$/i.test(q)) return { command: { kind: 'back' } }
  if (/^(?:go )?forward$|^aage(?: jao)?$|^आगे(?: जाओ)?$/i.test(q)) return { command: { kind: 'forward' } }
  if (/^(?:refresh|reload)(?: page)?$|^page refresh karo$/i.test(q)) return { command: { kind: 'reload' } }
  if (/^(?:scroll down|neeche jao|neeche scroll karo|नीचे स्क्रॉल करो)$/i.test(q)) return { command: { kind: 'scroll_down' } }
  if (/^(?:scroll up|upar jao|upar scroll karo|ऊपर स्क्रॉल करो)$/i.test(q)) return { command: { kind: 'scroll_up' } }
  if (/^(?:open first result|pehla result kholo|पहला रिजल्ट खोलो)$/i.test(q)) return { command: { kind: 'open_result', index: 1 } }
  const result = /^(?:open result|result) ([1-5])(?: kholo)?$/i.exec(q)
  if (result) return { command: { kind: 'open_result', index: Number(result[1]) } }
  if (/^(?:next|previous|last) tab$/i.test(q)) {
    const current = Math.max(0, tabs.findIndex((tab) => tab.id === activeTabId))
    const index = (current + (q.toLowerCase().startsWith('next') ? 1 : tabs.length - 1)) % tabs.length
    return tabs.length > 1 ? { command: { kind: 'switch', tabId: tabs[index].id } } : { reply: 'Only one tab is open.' }
  }
  const tab = /^(?:switch to|open|go to) tab (\d+)$|^tab (\d+) kholo$/i.exec(q)
  if (tab) { const target = tabs[Number(tab[1] || tab[2]) - 1]; return target ? { command: { kind: 'switch', tabId: target.id } } : { reply: 'That tab number is not open.' } }
  const hindiSearch = /^(amazon|flipkart|google)\s+(?:par|pe|पर)\s+(.+?)\s+(?:search karo|dhundo|dhoondo|सर्च करो|खोजो)$/i.exec(q)
  if (hindiSearch) return search(hindiSearch[2], hindiSearch[1].toLowerCase())
  const askSearch = /^(.+?)\s+(?:search karo|dhundo|dhoondo|सर्च करो|खोजो)$/i.exec(q)
  if (askSearch) return { reply: 'Kahan search karun—Amazon, Flipkart ya Google?', pendingSearch: askSearch[1] }
  const find = /^(?:search|find|dhundo|dhoondo)\s+(.+?)(?:\s+(?:on|par|pe)\s+(amazon|flipkart|google))?$/i.exec(q)
  if (find) return find[2] ? search(find[1], find[2].toLowerCase()) : { reply: 'Kahan search karun—Amazon, Flipkart ya Google?', pendingSearch: find[1] }
  const open = /^(?:open|visit|navigate to|go to)\s+(.+)$|^(.+?)\s+(?:kholo|khol do|खोलो)$/i.exec(q)
  if (open) {
    const target = (open[1] || open[2]).trim()
    const url = sites[target.toLowerCase()] || safeConversationUrl(target)
    return url ? { command: { kind: 'navigate', url } } : { reply: 'Please give a public HTTPS website address. Account changes, cart and checkout navigation are disabled.' }
  }
  if (/^(?:where am i|current page|abhi kaunsa page)[?]*$/i.test(q)) return { reply: `Current tab: ${tabs.find((tab) => tab.id === activeTabId)?.title || 'Untitled'}. ${tabs.find((tab) => tab.id === activeTabId)?.url || ''}` }
  return { analyze: true }
}
function search(query: string, site: string): ConversationPlan {
  const bases: Record<string, string> = { amazon: 'https://www.amazon.in/s?k=', flipkart: 'https://www.flipkart.com/search?q=', google: 'https://www.google.com/search?q=' }
  return { command: { kind: 'navigate', url: bases[site] + encodeURIComponent(query.trim()) } }
}
