import { planConversation, safeConversationUrl, type BrowserCommand } from '../utils/browserConversation.ts'
import type { ClaimedRuntimeWork, RuntimeReceipt, RuntimeReply, RuntimeTarget, RuntimeWork, RuntimeProduct } from '../types/assistant.ts'

export interface NavigationContext { tabId: string; url: string; target: RuntimeTarget }
export interface NavigationRunSnapshot {
  run_id: string; status: 'planned' | 'executing' | 'paused' | 'completed' | 'cancelled' | 'failed';
  message: string; can_resume: boolean;
  steps: { step_id: string; command: BrowserCommand; status: 'pending' | 'executing' | 'completed' | 'failed' | 'unknown' }[];
}
export interface NavigationExecutor {
  observe: () => Promise<NavigationContext>
  execute: (command: BrowserCommand, before: NavigationContext, stepId: string) => Promise<{ success: boolean; message: string }>
  stop: () => void
  changed: (run: NavigationRunSnapshot) => void
  observed?: (context: NavigationContext) => void
}
const visible = (context: NavigationContext) => context.target.active && context.target.window_focused
const sameDocument = (a: NavigationContext, b: NavigationContext) => a.tabId === b.tabId && a.url === b.url && a.target.document_id === b.target.document_id && a.target.tab_id === b.target.tab_id
export function validateNavigationCommands(commands: readonly BrowserCommand[]): BrowserCommand[] {
  if (!commands.length || commands.length > 8) throw new Error('A browser plan needs one to eight steps.')
  return commands.map((step) => {
    const required: Record<string, string[]> = { navigate: ['kind', 'url'], open_result: ['kind', 'index'], switch: ['kind', 'tabId'], back: ['kind'], forward: ['kind'], reload: ['kind'], scroll_down: ['kind'], scroll_up: ['kind'] }
    if (!required[step.kind] || Object.keys(step).sort().join() !== required[step.kind].sort().join()) throw new Error('Unsupported browser plan arguments.')
    if (step.kind === 'navigate' && safeConversationUrl(step.url) !== step.url) throw new Error('Unsafe navigation destination.')
    if (step.kind === 'open_result' && (!Number.isInteger(step.index) || step.index < 1 || step.index > 5)) throw new Error('Invalid result number.')
    if (step.kind === 'switch' && !/^[a-zA-Z0-9_.:-]{1,100}$/.test(step.tabId)) throw new Error('Invalid tab identity.')
    return Object.freeze({ ...step })
  })
}
function verified(command: BrowserCommand, before: NavigationContext, after: NavigationContext): boolean {
  if (!visible(after)) return false
  if (command.kind === 'switch') return after.tabId === command.tabId
  if (after.tabId !== before.tabId || after.target.tab_id !== before.target.tab_id) return false
  if (command.kind === 'navigate') {
    const expected = new URL(command.url), actual = new URL(after.url)
    return after.target.document_id !== before.target.document_id && expected.origin === actual.origin && expected.pathname === actual.pathname &&
      [...expected.searchParams].every(([key, value]) => actual.searchParams.get(key) === value)
  }
  if (command.kind === 'open_result') {
    const source = new URL(before.url), actual = new URL(after.url)
    return actual.origin === source.origin && /\/dp\/[a-z0-9]{10}(?:\/|$)|\/p\/itm[a-z0-9]+/i.test(actual.pathname) && after.target.document_id !== before.target.document_id
  }
  if (command.kind === 'scroll_down' || command.kind === 'scroll_up') return sameDocument(before, after)
  if (command.kind === 'reload') return before.url === after.url && before.target.document_id !== after.target.document_id
  return after.target.document_id !== before.target.document_id
}

/** One serial controller for rule/model plans. No retries, selectors or model prose. */
export class NavigationOrchestrator {
  private run: NavigationRunSnapshot | null = null
  private expected: NavigationContext | null = null
  private executor: NavigationExecutor | null = null
  private epoch = 0
  private paused = false
  private deadline = 0
  private active = false
  private activeEpoch = -1
  private clock: () => number
  private maxDurationMs: number
  constructor(clock: () => number = Date.now, maxDurationMs = 120000) { this.clock = clock; this.maxDurationMs = maxDurationMs }
  private publish() {
    if (this.run) this.executor?.changed(structuredClone(this.run))
  }
  snapshot() { return this.run ? structuredClone(this.run) : null }
  async start(commands: readonly BrowserCommand[], executor: NavigationExecutor): Promise<NavigationRunSnapshot> {
    const safe = validateNavigationCommands(commands)
    this.cancel()
    this.executor = executor; this.expected = null; this.paused = false
    const id = crypto.randomUUID()
    this.run = { run_id: id, status: 'planned', can_resume: false, message: 'Navigation plan ready.',
      steps: safe.map((command, index) => ({ step_id: `${id}:${index}`, command, status: 'pending' })) }
    this.deadline = this.clock() + this.maxDurationMs
    this.publish()
    return this.drive(this.epoch)
  }
  pause() { this.paused = true }
  cancel() {
    this.epoch++
    if (this.run && ['planned', 'executing', 'paused'].includes(this.run.status)) this.executor?.stop()
    if (this.run && ['planned', 'executing', 'paused'].includes(this.run.status)) {
      for (const step of this.run.steps) if (step.status === 'executing') step.status = 'unknown'
      this.run.status = 'cancelled'; this.run.can_resume = false; this.run.message = 'Browser plan cancelled; remaining steps will not run.'; this.publish()
    }
  }
  async resume(): Promise<NavigationRunSnapshot | null> {
    if (!this.run || this.run.status !== 'paused' || !this.run.can_resume || this.active) return this.snapshot()
    this.paused = false
    return this.drive(this.epoch)
  }
  private async drive(epoch: number): Promise<NavigationRunSnapshot> {
    const run = this.run!, executor = this.executor!
    this.active = true; this.activeEpoch = epoch
    try {
      for (const step of run.steps) {
        if (step.status === 'completed') continue
        if (epoch !== this.epoch) return structuredClone(run)
        if (this.clock() >= this.deadline) throw new Error('Navigation plan expired. Start a fresh request.')
        const before = await executor.observe()
        if (epoch !== this.epoch) return structuredClone(run)
        if (this.paused || !visible(before) || this.expected && !sameDocument(this.expected, before)) {
          this.expected ??= before
          run.status = 'paused'; run.can_resume = sameDocument(this.expected, before)
          run.message = this.expected && !sameDocument(this.expected, before) ? 'The browser target changed. Check the page and start a fresh request.' : 'Browser plan paused. Bring the same tab to the foreground, then Resume.'
          this.publish(); return structuredClone(run)
        }
        this.expected = before
        step.status = 'executing'; run.status = 'executing'; run.can_resume = false
        run.message = 'Executing one browser step and waiting for evidence.'; this.publish()
        let timer: ReturnType<typeof setTimeout> | undefined
        try {
          const outcome = await Promise.race([executor.execute(step.command, before, step.step_id),
            new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Navigation timed out; its result is unconfirmed.')), Math.min(31000, this.deadline - this.clock())) })])
          if (epoch !== this.epoch) return structuredClone(run)
          if (this.clock() >= this.deadline) throw new Error('Navigation plan expired; its last result is unconfirmed.')
          if (!outcome.success) { step.status = 'failed'; throw new Error(outcome.message) }
          const after = await executor.observe()
          if (epoch !== this.epoch) return structuredClone(run)
          if (!verified(step.command, before, after)) {
            step.status = 'unknown'; run.status = 'paused'; run.can_resume = false
            run.message = 'Navigation outcome could not be verified. Check the page and start a fresh request.'
            this.publish(); return structuredClone(run)
          }
          step.status = 'completed'; this.expected = after
          executor.observed?.(after); this.publish()
        } finally { if (timer) clearTimeout(timer) }
      }
      run.status = 'completed'; run.can_resume = false; run.message = 'Browser plan completed and each step was verified.'; this.publish()
    } catch (error) {
      if (epoch === this.epoch) {
        const pending = run.steps.find((step) => step.status === 'executing')
        if (pending) pending.status = 'unknown'
        run.status = 'failed'; run.can_resume = false; run.message = error instanceof Error ? error.message : 'Browser plan failed.'
        executor.stop(); this.publish()
      }
    } finally { if (this.activeEpoch === epoch) this.active = false }
    return structuredClone(run)
  }
}

export function localNavigationPlan(message: string, tabs: { id: string; title: string; url: string }[], activeTabId: string, pendingSearch?: string): BrowserCommand[] | null {
  if (/\b(do not|dont|don't|mat|nahi|buy|checkout|pay|delete|login|purchase)\b|नहीं|मत/i.test(message)) return null
  const pieces = message.split(/\s*(?:;|\band then\b|\bthen\b|\baur phir\b|\bphir\b|और फिर|\s+aur\s+|\s+and\s+(?=(?:open|search|find|go|scroll|reload|refresh)\b))\s*/i)
  if (!pieces.length || pieces.length > 8) return null
  const commands: BrowserCommand[] = []
  let currentSite: 'amazon' | 'flipkart' | 'google' | null = null
  for (const part of pieces) {
    const plan = planConversation(part, tabs, pieces.length === 1 ? pendingSearch : undefined, activeTabId)
    if (plan.command) {
      commands.push(plan.command)
      if (plan.command.kind === 'navigate') { const host = new URL(plan.command.url).hostname; currentSite = host.includes('amazon.in') ? 'amazon' : host.includes('flipkart.com') ? 'flipkart' : host.includes('google.com') ? 'google' : null }
    } else if (plan.pendingSearch && currentSite) {
      const bases = { amazon: 'https://www.amazon.in/s?k=', flipkart: 'https://www.flipkart.com/search?q=', google: 'https://www.google.com/search?q=' }
      commands.push({ kind: 'navigate', url: bases[currentSite] + encodeURIComponent(plan.pendingSearch) })
    } else return null
  }
  try { return validateNavigationCommands(commands) } catch { return null }
}


interface RuntimeWorkApi {
  getAiWork: (conversationId: string) => Promise<RuntimeWork[]>
  claimAiWork: (conversationId: string, dispatchId: string, claim: { claim_id: string; expected_version: number; target: RuntimeTarget; origin: string }) => Promise<ClaimedRuntimeWork>
  submitAiReceipt: (conversationId: string, dispatchId: string, receipt: RuntimeReceipt) => Promise<RuntimeReply>
}
interface CapturedProduct {
  url: string; document_id: string; captured_at: string; retailer: string; retailer_product_id: string;
  title: string; price: string; currency: string; availability: string; specs?: Record<string, string>
}
export interface RuntimeReadExecutor {
  observe: () => Promise<NavigationContext>
  capture: (tabId: string) => Promise<{ success: boolean; snapshot?: CapturedProduct; error?: string }>
  navigate: (url: string, before: NavigationContext, stepId: string) => Promise<{ success: boolean; message: string }>
  alive: () => boolean
  progress?: (message: string, dispatchId: string, status: 'executing' | 'completed' | 'failed') => void
}
export function capturedRuntimeProduct(capture: CapturedProduct): RuntimeProduct {
  const knownPrice = /^\d{1,12}(?:\.\d{1,2})?$/.test(capture.price) && Number(capture.price) > 0 && /^[A-Z]{3}$/.test(capture.currency)
  return { product_id: `${capture.retailer}:${capture.retailer_product_id}`.slice(0, 100), snapshot_id: crypto.randomUUID(),
    document_id: capture.document_id, title: capture.title, observed_at: capture.captured_at,
    price: knownPrice ? capture.price : null, currency: knownPrice ? capture.currency : null,
    in_stock: capture.availability === 'in_stock' ? true : capture.availability === 'out_of_stock' ? false : null,
    specs: capture.specs || {}, variants: [], selected_variant_id: null }
}
const runtimeClaims = new Map<string, string>()
/** Drain owned durable work serially. A missing site adapter is a failed receipt, never ignored work. */
export async function drainRuntimeNavigation(api: RuntimeWorkApi, initial: RuntimeReply, executor: RuntimeReadExecutor): Promise<{ reply: RuntimeReply; note?: string }> {
  let reply = initial
  let note: string | undefined
  for (let step = 0; step < 8 && reply.work_ids.length; step++) {
    if (!executor.alive()) return { reply, note: 'Browser work stopped before dispatch.' }
    const available = await api.getAiWork(reply.conversation_id)
    const work = available.find((item) => reply.work_ids.includes(item.dispatch_id))
    if (!work) return { reply, note: 'No dispatch is available. Check the conversation before continuing.' }
    const before = await executor.observe()
    if (!executor.alive() || !visible(before)) return { reply, note: 'Pending work needs the same foreground browser tab. Nothing was executed.' }
    const key = reply.conversation_id + ':' + work.dispatch_id
    if (runtimeClaims.has(key)) return { reply, note: 'This work was already attempted. Check or cancel the pending conversation; it will not be replayed.' }
    if (runtimeClaims.size >= 500) return { reply, note: 'Browser work journal is full. Stop pending work and restart before a new request.' }
    const claimId = crypto.randomUUID()
    runtimeClaims.set(key, claimId)
    const claimed = await api.claimAiWork(reply.conversation_id, work.dispatch_id, { claim_id: claimId, expected_version: reply.version, target: before.target, origin: new URL(before.url).origin })
    if (!executor.alive()) return { reply, note: 'Claimed work stopped before browser execution.' }
    executor.progress?.('Executing claimed read-only browser work.', work.dispatch_id, 'executing')
    const receipt: RuntimeReceipt = { event_id: crypto.randomUUID(), expected_version: reply.version, claim_token: claimed.claim_token, origin: new URL(before.url).origin }
    if (claimed.kind === 'browser') {
      const tool = (claimed.work.tool as { name?: string } | undefined)?.name
      if (tool === 'read_product' || tool === 'read_specs') {
        const capture = await executor.capture(before.tabId)
        const after = await executor.observe()
        if (!executor.alive()) return { reply, note: 'Capture stopped; no result was submitted.' }
        if (capture.success && capture.snapshot && sameDocument(before, after) && visible(after) && capture.snapshot.document_id === after.target.document_id) {
          receipt.browser = { outcome: 'observed', target: after.target, observed_at: capture.snapshot.captured_at, product: capturedRuntimeProduct(capture.snapshot) }
        } else receipt.browser = { outcome: 'failed', target: before.target, observed_at: new Date().toISOString(), error_code: 'PRODUCT_READ_UNAVAILABLE' }
      } else {
        receipt.browser = { outcome: 'failed', target: before.target, observed_at: new Date().toISOString(), error_code: 'READ_ONLY_EXECUTOR' }
        note = 'This browser executor does not click cart, variant or purchase controls.'
      }
    } else if (claimed.kind === 'navigation') {
      const offer = claimed.work.offer as { url?: string; identity?: { brand: string; model: string; condition: string; attributes: Record<string, string> } } | undefined
      const url = offer?.url
      let after = before
      let capture: Awaited<ReturnType<RuntimeReadExecutor['capture']>> | null = null
      if (url && safeConversationUrl(url) === url) {
        const result = await executor.navigate(url, before, work.dispatch_id)
        if (!executor.alive()) return { reply, note: 'Navigation stopped; its result is unconfirmed.' }
        after = await executor.observe()
        if (result.success && verified({ kind: 'navigate', url }, before, after)) capture = await executor.capture(after.tabId)
      }
      const specs = capture?.snapshot?.specs || {}
      const lower = Object.fromEntries(Object.entries(specs).map(([key, value]) => [key.toLowerCase(), value]))
      const identity = { brand: lower.brand, model: lower.model || lower['model name'] || lower['model number'], condition: lower.condition,
        attributes: Object.fromEntries(Object.keys(offer?.identity?.attributes || {}).map((key) => [key, lower[key.toLowerCase()]])) }
      const normalize = (value: unknown) => typeof value === 'string' ? value.trim().toLowerCase().replace(/\s+/g, ' ') : null
      const matches = offer?.identity && ['brand', 'model', 'condition'].every((key) => normalize(identity[key as 'brand']) && normalize(identity[key as 'brand']) === normalize(offer.identity![key as 'brand'])) &&
        Object.entries(offer.identity.attributes).every(([key, value]) => normalize(identity.attributes[key]) === normalize(value))
      receipt.origin = new URL(after.url).origin
      if (capture?.success && capture.snapshot && matches && capture.snapshot.document_id === after.target.document_id && visible(after)) {
        receipt.navigation = { status: 'opened', observed_at: capture.snapshot.captured_at, url,
          target: after.target, identity: { ...identity, condition: normalize(identity.condition) }, product: capturedRuntimeProduct(capture.snapshot) }
      } else {
        receipt.navigation = { status: 'failed', observed_at: new Date().toISOString() }
        note = 'The destination could not be verified against the selected offer identity. No product-match claim was made.'
      }
    } else {
      // Search navigation can work without pretending that snippets prove seller/variant/delivery totals.
      const url = claimed.work.url
      let after = before
      if (typeof url === 'string' && safeConversationUrl(url) === url) {
        await executor.navigate(url, before, work.dispatch_id)
        if (!executor.alive()) return { reply, note: 'Search navigation stopped.' }
        after = await executor.observe()
      }
      receipt.origin = new URL(after.url).origin
      receipt.search = { status: 'blocked', target: after.target, observed_at: new Date().toISOString(), offers: [] }
      note = 'Verified cross-store offer extraction is unavailable for this adapter. Search pages may be opened, but no cheapest-price claim was made.'
    }
    if (!executor.alive()) return { reply, note: 'Browser work stopped before receipt submission.' }
    try {
      reply = await api.submitAiReceipt(reply.conversation_id, work.dispatch_id, receipt)
      const observed = receipt.browser?.outcome === 'observed' || receipt.navigation?.status === 'opened' || receipt.search?.status === 'complete'
      executor.progress?.(observed ? 'Browser evidence accepted.' : 'Browser work acknowledged without verified result.', work.dispatch_id, observed ? 'completed' : 'failed')
    } catch (error) {
      executor.progress?.('Browser receipt could not be confirmed.', work.dispatch_id, 'failed')
      throw error
    }
  }
  if (reply.work_ids.length) note = 'The eight-step browser limit was reached. Check the conversation before continuing.'
  return { reply, note }
}
