import { test } from 'node:test'
import assert from 'node:assert/strict'
import { NavigationOrchestrator, localNavigationPlan, drainRuntimeNavigation, type NavigationContext, type NavigationExecutor } from '../src/services/navigationOrchestrator.ts'
import type { BrowserCommand } from '../src/utils/browserConversation.ts'
import type { RuntimeReply, ClaimedRuntimeWork, RuntimeReceipt } from '../src/types/assistant.ts'
const tabs = [{ id: 'a', title: 'Search', url: 'https://www.google.com/' }]
function fixture() {
  let context: NavigationContext = { tabId: 'a', url: 'https://www.google.com/', target: { tab_id: 7, document_id: 'd0', active: true, window_focused: true } }
  let serial = 0, stops = 0
  const calls: { command: BrowserCommand; id: string }[] = []
  const snapshots: string[] = []
  const executor: NavigationExecutor = {
    observe: async () => structuredClone(context),
    execute: async (command, _before, id) => {
      calls.push({ command, id })
      if (command.kind === 'navigate') context.url = command.url
      if (command.kind === 'open_result') context.url = 'https://www.amazon.in/dp/B09V3KXJPB'
      if (command.kind === 'switch') context.tabId = command.tabId
      if (command.kind !== 'scroll_down' && command.kind !== 'scroll_up') context.target.document_id = 'd' + ++serial
      return { success: true, message: 'native load receipt' }
    },
    stop: () => { stops++ }, changed: (run) => { snapshots.push(run.status) },
  }
  return { executor, calls, snapshots, stops: () => stops, context: () => context }
}
test('compound navigation becomes one ordered plan with inherited search site', async () => {
  const steps = localNavigationPlan('Amazon kholo aur headphones search karo phir pehla result kholo', tabs, 'a')!
  assert.equal(steps.length, 3)
  assert.deepEqual(steps[1], { kind: 'navigate', url: 'https://www.amazon.in/s?k=headphones' })
  const f = fixture(), runner = new NavigationOrchestrator()
  const run = await runner.start(steps, f.executor)
  assert.equal(run.status, 'completed')
  assert.ok(run.steps.every((step) => step.status === 'completed'))
  assert.equal(new Set(f.calls.map((call) => call.id)).size, 3)
  assert.equal(f.context().url, 'https://www.amazon.in/dp/B09V3KXJPB')
})
test('invalid or unsafe tail means no partial browser plan is executed', async () => {
  assert.equal(localNavigationPlan('open amazon then checkout', tabs, 'a'), null)
  assert.equal(localNavigationPlan('open amazon then run arbitrary javascript', tabs, 'a'), null)
  const f = fixture()
  await assert.rejects(new NavigationOrchestrator().start([{ kind: 'navigate', url: 'https://www.amazon.in/' }, { kind: 'navigate', url: 'javascript:alert(1)' }], f.executor), /Unsafe/)
  assert.equal(f.calls.length, 0)
})
test('native failure stops the chain and is never retried', async () => {
  const f = fixture()
  f.executor.execute = async (command, _context, id) => { f.calls.push({ command, id }); return { success: false, message: 'DNS failed' } }
  const result = await new NavigationOrchestrator().start([{ kind: 'navigate', url: 'https://www.amazon.in/' }, { kind: 'scroll_down' }], f.executor)
  assert.equal(result.status, 'failed')
  assert.equal(f.calls.length, 1)
  assert.equal(result.steps[1].status, 'pending')
})
test('pause finishes the current verified step and resume does not replay it', async () => {
  const f = fixture(), runner = new NavigationOrchestrator()
  const execute = f.executor.execute
  f.executor.execute = async (...args) => { const result = await execute(...args); if (f.calls.length === 1) runner.pause(); return result }
  const paused = await runner.start([{ kind: 'navigate', url: 'https://www.amazon.in/' }, { kind: 'scroll_down' }], f.executor)
  assert.equal(paused.status, 'paused')
  assert.equal(paused.can_resume, true)
  assert.equal(f.calls.length, 1)
  const done = await runner.resume()
  assert.equal(done?.status, 'completed')
  assert.equal(f.calls.length, 2)
  assert.equal(f.calls[0].id, paused.steps[0].step_id)
})
test('focus is required before dispatch and explicit resume rechecks it', async () => {
  const f = fixture(), runner = new NavigationOrchestrator()
  f.context().target.window_focused = false
  const paused = await runner.start([{ kind: 'scroll_down' }], f.executor)
  assert.equal(paused.status, 'paused')
  assert.equal(f.calls.length, 0)
  f.context().target.window_focused = true
  assert.equal((await runner.resume())?.status, 'completed')
})
test('manual document change between steps prevents the remaining actions', async () => {
  const f = fixture(), runner = new NavigationOrchestrator()
  const observe = f.executor.observe
  let reads = 0
  f.executor.observe = async () => { if (++reads === 3) f.context().target.document_id = 'foreign-document'; return observe() }
  const run = await runner.start([{ kind: 'navigate', url: 'https://www.amazon.in/' }, { kind: 'scroll_down' }], f.executor)
  assert.equal(run.status, 'paused')
  assert.equal(run.can_resume, false)
  assert.equal(f.calls.length, 1)
  await runner.resume()
  assert.equal(f.calls.length, 1)
})
test('an unverified redirect is unknown and cannot be resumed as a successful step', async () => {
  const f = fixture()
  f.executor.execute = async () => { f.context().url = 'https://attacker.test/'; f.context().target.document_id = 'redirect'; return { success: true, message: 'loaded' } }
  const runner = new NavigationOrchestrator()
  const result = await runner.start([{ kind: 'navigate', url: 'https://www.amazon.in/' }], f.executor)
  assert.equal(result.steps[0].status, 'unknown')
  assert.equal(result.can_resume, false)
})
test('Stop fences a late result and never dispatches the next step', async () => {
  const f = fixture(), runner = new NavigationOrchestrator()
  let release: () => void = () => {}, entered: () => void = () => {}
  const started = new Promise<void>((resolve) => { entered = resolve })
  f.executor.execute = async (command, _context, id) => { f.calls.push({ command, id }); entered(); await new Promise<void>((resolve) => { release = resolve }); return { success: true, message: 'late' } }
  const pending = runner.start([{ kind: 'navigate', url: 'https://www.amazon.in/' }, { kind: 'scroll_down' }], f.executor)
  await started; runner.cancel(); release()
  const result = await pending
  assert.equal(result.status, 'cancelled')
  assert.equal(result.steps[0].status, 'unknown')
  assert.equal(f.calls.length, 1)
})
test('deadline expiry after a browser effect prevents completion and further dispatch', async () => {
  const f = fixture()
  let clock = 0
  const execute = f.executor.execute
  f.executor.execute = async (...args) => { const result = await execute(...args); clock = 120001; return result }
  const result = await new NavigationOrchestrator(() => clock).start([{ kind: 'navigate', url: 'https://www.amazon.in/' }, { kind: 'scroll_down' }], f.executor)
  assert.equal(result.status, 'failed')
  assert.equal(result.steps[0].status, 'unknown')
  assert.equal(f.calls.length, 1)
})
const runtimeReply = (ids: string[]): RuntimeReply => ({ conversation_id: crypto.randomUUID(), session_id: 'session', version: 3, work_ids: ids } as RuntimeReply)
test('durable browser work is claimed, captured and acknowledged rather than ignored', async () => {
  const f = fixture(), initial = runtimeReply(['read1']), receipts: RuntimeReceipt[] = []
  const api = {
    getAiWork: async () => [{ dispatch_id: 'read1', kind: 'browser' as const, status: 'pending' as const, version: 3, deadline_at: '2099-01-01T00:00:00Z' }],
    claimAiWork: async () => ({ dispatch_id: 'read1', kind: 'browser', claim_token: 'token', work: { tool: { name: 'read_product' } } } as ClaimedRuntimeWork),
    submitAiReceipt: async (_id: string, _dispatch: string, receipt: RuntimeReceipt) => { receipts.push(receipt); return { ...initial, version: 4, work_ids: [] } },
  }
  const complete = await drainRuntimeNavigation(api, initial, { observe: f.executor.observe, alive: () => true, navigate: async () => ({ success: true, message: '' }),
    capture: async () => ({ success: true, snapshot: { url: f.context().url, document_id: 'd0', captured_at: new Date().toISOString(), retailer: 'amazon', retailer_product_id: 'B09V3KXJPB', title: 'Observed', price: '100.00', currency: 'INR', availability: 'unknown', specs: { Battery: '5000 mAh' } } }) })
  assert.equal(complete.reply.version, 4)
  assert.equal(receipts[0].browser?.outcome, 'observed')
  assert.equal(receipts[0].browser?.product?.in_stock, null)
  assert.equal(receipts[0].browser?.product?.specs.Battery, '5000 mAh')
})
test('mutation work is failed without invoking any browser control', async () => {
  const f = fixture(), initial = runtimeReply(['mutation']), receipts: RuntimeReceipt[] = []
  const api = {
    getAiWork: async () => [{ dispatch_id: 'mutation', kind: 'browser' as const, status: 'pending' as const, version: 3, deadline_at: '2099-01-01T00:00:00Z' }],
    claimAiWork: async () => ({ dispatch_id: 'mutation', kind: 'browser', claim_token: 'token', work: { tool: { name: 'add_to_cart' } } } as ClaimedRuntimeWork),
    submitAiReceipt: async (_id: string, _dispatch: string, receipt: RuntimeReceipt) => { receipts.push(receipt); return { ...initial, work_ids: [] } },
  }
  await drainRuntimeNavigation(api, initial, { observe: f.executor.observe, alive: () => true,
    navigate: async () => { throw new Error('Must not navigate') }, capture: async () => { throw new Error('Must not capture cart') } })
  assert.equal(receipts[0].browser?.error_code, 'READ_ONLY_EXECUTOR')
})

test('offer navigation acknowledges only an identity read from the actual destination specs', async () => {
  for (const hasCondition of [true, false]) {
    const f = fixture(), initial = runtimeReply(['offer']), receipts: RuntimeReceipt[] = []
    const url = 'https://www.amazon.in/dp/B09V3KXJPB'
    const api = {
      getAiWork: async () => [{ dispatch_id: 'offer', kind: 'navigation' as const, status: 'pending' as const, version: 3, deadline_at: '2099-01-01T00:00:00Z' }],
      claimAiWork: async () => ({ dispatch_id: 'offer', kind: 'navigation', claim_token: 'token', work: { offer: { url, identity: { brand: 'Example', model: 'Phone', condition: 'new', attributes: { colour: 'black' } } } } } as ClaimedRuntimeWork),
      submitAiReceipt: async (_id: string, _dispatch: string, receipt: RuntimeReceipt) => { receipts.push(receipt); return { ...initial, work_ids: [] } },
    }
    await drainRuntimeNavigation(api, initial, { observe: f.executor.observe, alive: () => true,
      navigate: (url, before, id) => f.executor.execute({ kind: 'navigate', url }, before, id),
      capture: async () => ({ success: true, snapshot: { url, document_id: f.context().target.document_id, captured_at: new Date().toISOString(), retailer: 'amazon', retailer_product_id: 'B09V3KXJPB', title: 'Phone', price: '100.00', currency: 'INR', availability: 'unknown', specs: { Brand: 'Example', 'Model Name': 'Phone', Colour: 'black', ...(hasCondition ? { Condition: 'new' } : {}) } } }),
    })
    assert.equal(receipts[0].navigation?.status, hasCondition ? 'opened' : 'failed')
    assert.equal(receipts[0].origin, 'https://www.amazon.in')
    if (!hasCondition) assert.equal(receipts[0].navigation?.product, undefined)
  }
})
test('search work opens its page but does not invent verified offers or a delivered total', async () => {
  const f = fixture(), initial = runtimeReply(['market']), receipts: RuntimeReceipt[] = []
  const api = {
    getAiWork: async () => [{ dispatch_id: 'market', kind: 'market' as const, status: 'pending' as const, version: 3, deadline_at: '2099-01-01T00:00:00Z' }],
    claimAiWork: async () => ({ dispatch_id: 'market', kind: 'market', claim_token: 'token', work: { url: 'https://www.flipkart.com/search?q=headphones' } } as ClaimedRuntimeWork),
    submitAiReceipt: async (_id: string, _dispatch: string, receipt: RuntimeReceipt) => { receipts.push(receipt); return { ...initial, work_ids: [] } },
  }
  const result = await drainRuntimeNavigation(api, initial, { observe: f.executor.observe, alive: () => true,
    navigate: (url, before, id) => f.executor.execute({ kind: 'navigate', url }, before, id), capture: async () => ({ success: false }),
  })
  assert.equal(f.calls.length, 1)
  assert.equal(receipts[0].search?.status, 'blocked')
  assert.deepEqual(receipts[0].search?.offers, [])
  assert.ok(result.note?.includes('no cheapest-price claim'))
})
