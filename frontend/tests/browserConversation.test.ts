import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'
import { transformSync } from 'esbuild'
import { planConversation, safeConversationUrl, updateShoppingPreferences } from '../src/utils/browserConversation.ts'

const tabs = [{ id: 'a', title: 'First', url: 'https://www.google.com/' }, { id: 'b', title: 'Second', url: 'https://www.amazon.in/' }]
test('explicit shopping preferences survive navigation and update across turns', () => {
  const first = updateShoppingPreferences('budget 20k, need 8 GB RAM black', { specs: {} })
  assert.deepEqual(first, { budget: '20000.00', specs: { RAM: '8gb', Colour: 'black' } })
  const afterNavigation = updateShoppingPreferences('Amazon kholo', first)
  assert.deepEqual(afterNavigation, first)
  const updated = updateShoppingPreferences('budget 15000', afterNavigation)
  assert.equal(updated.budget, '15000.00')
  assert.equal(updated.specs.RAM, '8gb')
  assert.equal(updateShoppingPreferences('clear budget', updated).budget, undefined)
})
test('questions, negation and contradictory requirements do not overwrite memory', () => {
  const original = { budget: '20000.00', specs: { RAM: '8gb' } }
  for (const question of ['does it have 4GB RAM?', 'not under 1000', 'under 20,00', 'budget 1000 or budget 2000', 'need 4GB RAM and 8GB RAM']) assert.deepEqual(updateShoppingPreferences(question, original), original)
})

test('greeting and help work without a product capture', () => {
  assert.match(planConversation('namaste', tabs).reply!, /Saarthi/)
  assert.match(planConversation('help', tabs).reply!, /checkout/)
})
test('search clarification remembers the explicit query and chosen site', () => {
  const first = planConversation('search headphones', tabs)
  assert.equal(first.pendingSearch, 'headphones')
  assert.equal(first.command, undefined)
  assert.deepEqual(planConversation('Amazon par', tabs, first.pendingSearch).command, { kind: 'navigate', url: 'https://www.amazon.in/s?k=headphones' })
  assert.equal(planConversation('search headphones on flipkart', tabs).command?.kind, 'navigate')
})
test('common Hindi voice transcripts and Hinglish use the same command flow', () => {
  assert.deepEqual(planConversation('अमेज़न खोलो', tabs).command, { kind: 'navigate', url: 'https://www.amazon.in/' })
  assert.deepEqual(planConversation('अमेज़न पर हेडफोन खोजो', tabs).command, { kind: 'navigate', url: 'https://www.amazon.in/s?k=' + encodeURIComponent('हेडफोन') })
  assert.equal(planConversation('headphones search karo', tabs).pendingSearch, 'headphones')
  assert.equal(planConversation('please go back', tabs).command?.kind, 'back')
})
test('unsafe destinations are rejected; search input remains encoded data', () => {
  for (const url of ['javascript:alert(1)', 'http://example.com', 'https://user:pass@example.com', 'https://localhost/', 'https://127.1/', 'https://0x7f000001/', 'https://192.168.1.1/', 'https://shop.local/', 'https://example.com/checkout']) assert.equal(safeConversationUrl(url), null, url)
  assert.equal(planConversation('open javascript:alert(1)', tabs).command, undefined)
  const command = planConversation('search a&admin=true on amazon', tabs).command
  assert.ok(command?.kind === 'navigate')
  assert.equal(new URL(command.url).searchParams.get('k'), 'a&admin=true')
  assert.equal(new URL(command.url).searchParams.get('admin'), null)
})
test('negation, compound requests and questions never dispatch navigation', () => {
  for (const question of ['do not open amazon', 'amazon mat kholo', 'open amazon then checkout', 'can you explain how to open amazon?']) assert.equal(planConversation(question, tabs).command, undefined)
  assert.equal(planConversation('battery kitni hai?', tabs).analyze, true)
})
test('tab numbers use visible order and relative navigation uses current tab', () => {
  assert.deepEqual(planConversation('next tab', tabs, undefined, 'b').command, { kind: 'switch', tabId: 'a' })
  assert.deepEqual(planConversation('open tab 2', tabs, undefined, 'b').command, { kind: 'switch', tabId: 'b' })
  assert.equal(planConversation('open tab 99', tabs).command, undefined)
  assert.match(planConversation('where am i', tabs, undefined, 'b').reply!, /Second/)
})
test('scroll commands are explicit, bounded read-only operations', () => {
  assert.deepEqual(planConversation('scroll down', tabs).command, { kind: 'scroll_down' })
  assert.deepEqual(planConversation('upar jao', tabs).command, { kind: 'scroll_up' })
  assert.equal(planConversation('do not scroll down', tabs).command, undefined)
})

function nativeHarness() {
  const source = readFileSync(new URL('../electron/main.ts', import.meta.url), 'utf8')
  const start = source.indexOf('const conversationNavigationCancels =')
  const end = source.indexOf("ipcMain.on('browser:navigate'", start)
  const code = transformSync(source.slice(start, end), { loader: 'ts' }).code
  let handler: (event: string, data: Record<string, unknown>) => Promise<{ success: boolean; message: string }>
  const wc = Object.assign(new EventEmitter(), {
    id: 7, current: 'https://www.google.com/', calls: 0, stopped: false,
    getURL() { return this.current },
    async loadURL(url: string) { this.calls++; this.current = url },
    stop() { this.stopped = true }, reload() { this.calls++ },
    async executeJavaScript(_code: string): Promise<unknown> { return true },
    navigationHistory: { canGoBack: () => false, canGoForward: () => false },
  })
  new Function('ipcMain', 'assertTrustedUi', 'views', 'activeTabId', 'mainWindow', 'documentVersions', `const switchActiveTab = (id) => { activeTabId = id };\n${code}`)(
    { handle: (_name: string, fn: typeof handler) => { handler = fn } },
    (event: string) => { if (event !== 'trusted') throw new Error('Untrusted frame') },
    new Map([['a', { webContents: wc }], ['b', { webContents: wc }]]), 'a', { isFocused: () => true }, new Map(),
  )
  return { call: (data: Record<string, unknown>, event = 'trusted') => handler!(event, { tabId: 'a', expectedUrl: 'https://www.google.com/', ...data }), wc }
}
test('native executor rejects foreign frames, stale tabs and unsafe URLs', async () => {
  const { call, wc } = nativeHarness()
  await assert.rejects(call({ kind: 'navigate', url: 'https://example.com/' }, 'foreign'), /Untrusted/)
  for (const data of [{ kind: 'navigate', url: 'javascript:alert(1)' }, { kind: 'navigate', url: 'https://127.0.0.1/' }, { kind: 'navigate', url: 'https://example.com/checkout' }, { kind: 'navigate', url: 'https://example.com/', tabId: 'other' }, { kind: 'navigate', url: 'https://example.com/', expectedUrl: 'https://old.test/' }]) assert.equal((await call(data)).success, false)
  assert.equal(wc.calls, 0)
})
test('actual page-load receipt resolves navigation and cleans listeners', async () => {
  const { call, wc } = nativeHarness()
  const promise = call({ kind: 'navigate', url: 'https://example.com/' })
  wc.emit('did-finish-load')
  assert.equal((await promise).success, true)
  assert.equal(wc.listenerCount('did-finish-load'), 0)
  assert.equal(wc.listenerCount('did-fail-load'), 0)
})
test('failed loads and unavailable history do not claim success', async () => {
  const { call, wc } = nativeHarness()
  assert.equal((await call({ kind: 'back' })).success, false)
  const promise = call({ kind: 'navigate', url: 'https://example.com/' })
  wc.emit('did-fail-load', {}, -105, 'private diagnostic', 'https://example.com/', true)
  const result = await promise
  assert.equal(result.success, false)
  assert.ok(!result.message.includes('private diagnostic'))
})
test('stop settles pending navigation after its URL changes', async () => {
  const { call, wc } = nativeHarness()
  const promise = call({ kind: 'navigate', url: 'https://example.com/' })
  assert.equal((await call({ kind: 'stop' })).success, true)
  assert.equal((await promise).success, false)
  assert.equal(wc.stopped, true)
  assert.equal(wc.listenerCount('did-finish-load'), 0)
})
test('native scroll reports movement and tab switching requires an existing tab', async () => {
  const { call } = nativeHarness()
  assert.equal((await call({ kind: 'scroll_down' })).success, true)
  assert.equal((await call({ kind: 'switch', targetTabId: 'missing' })).success, false)
  assert.equal((await call({ kind: 'switch', targetTabId: 'b' })).success, true)
})
test('opening a shopping result uses canonical observed links and a load receipt', async () => {
  const { call, wc } = nativeHarness()
  wc.current = 'https://www.amazon.in/s?k=headphones'
  wc.executeJavaScript = async () => ['https://www.amazon.in/dp/B09V3KXJPB']
  const promise = call({ kind: 'open_result', index: 1, expectedUrl: wc.current })
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(wc.current, 'https://www.amazon.in/dp/B09V3KXJPB')
  wc.emit('did-finish-load')
  assert.equal((await promise).success, true)
})
test('result links cannot escape retailer origin and stop invalidates a pending read', async () => {
  const { call, wc } = nativeHarness()
  wc.current = 'https://www.amazon.in/s?k=headphones'
  wc.executeJavaScript = async () => ['https://attacker.test/dp/B09V3KXJPB']
  assert.equal((await call({ kind: 'open_result', index: 1, expectedUrl: wc.current })).success, false)
  let complete: (value: unknown) => void = () => {}
  wc.executeJavaScript = () => new Promise((resolve) => { complete = resolve })
  const pending = call({ kind: 'open_result', index: 1, expectedUrl: wc.current })
  await call({ kind: 'stop', expectedUrl: wc.current })
  complete(['https://www.amazon.in/dp/B09V3KXJPB'])
  assert.equal((await pending).success, false)
  assert.equal(wc.calls, 0)
})
test('packaged result extraction handles product links as data and drops tracking', async () => {
  const { call, wc } = nativeHarness()
  wc.current = 'https://www.amazon.in/s?k=headphones'
  wc.executeJavaScript = async (script) => {
    const urls = ['https://www.amazon.in/dp/B09V3KXJPB?tracking=private', 'https://www.amazon.in/checkout', 'https://attacker.test/dp/B09V3KXJPB']
    const nodes = urls.map((href) => ({ closest: () => ({ href, getBoundingClientRect: () => ({ width: 100, height: 40 }) }) }))
    return new Function('document', 'location', 'getComputedStyle', `return ${script}`)(
      { querySelectorAll: () => nodes }, new URL(wc.current), () => ({ display: 'block', visibility: 'visible' }),
    )
  }
  const pending = call({ kind: 'open_result', index: 1, expectedUrl: wc.current })
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(wc.current, 'https://www.amazon.in/dp/B09V3KXJPB')
  wc.emit('did-finish-load')
  assert.equal((await pending).success, true)
})

test('native step journal deduplicates execution and refuses identity reuse', async () => {
  const { call, wc } = nativeHarness()
  const data = { kind: 'navigate', url: 'https://example.com/', executionId: 'stable-step', expectedDocumentId: 'document-7-0' }
  const first = call(data), duplicate = call(data)
  assert.equal(wc.calls, 1)
  assert.equal((await call({ ...data, url: 'https://other.test/' })).success, false)
  wc.emit('did-finish-load')
  assert.equal((await first).success, true)
  assert.deepEqual(await duplicate, await first)
  assert.equal((await call(data)).success, true)
  assert.equal(wc.calls, 1)
})
test('an orchestrated step cannot execute against a reloaded same-URL document', async () => {
  const { call, wc } = nativeHarness()
  assert.equal((await call({ kind: 'navigate', url: 'https://example.com/', executionId: 'stale-step', expectedDocumentId: 'document-7-old' })).success, false)
  assert.equal(wc.calls, 0)
})
