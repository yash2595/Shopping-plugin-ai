import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { BrowserShell } from './components/browser/BrowserShell'
import { INITIAL_TABS } from './data/mockBrowserData'
import type { AgentEvent, AgentRunState } from './types/agentEvents'
import type { BackendConnectionStatus } from './types/assistant'
import type { BrowserTab } from './types/browser'
import type { OrbOverlayBounds } from './types/electron'
import type { AnalysisProduct, ShoppingAnalysis } from './types/assistant'
import { apiClient, NetworkError } from './services/apiClient'
import { NavigationOrchestrator, drainRuntimeNavigation, localNavigationPlan, type NavigationRunSnapshot } from './services/navigationOrchestrator'
import type { BrowserCommand } from './utils/browserConversation'
import { planConversation, updateShoppingPreferences } from './utils/browserConversation'
import type { RuntimeReply } from './types/assistant'
import type { ConversationTurn } from './types/assistant'
import { buildReadDiagnostic, isSupportedProductAddress, rawCaptureFallback } from './utils/snapshotExtractor'

function App() {
  const [tabs, setTabs] = useState<BrowserTab[]>(INITIAL_TABS)
  const [activeTabId, setActiveTabId] = useState<string>(INITIAL_TABS[0].id)
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([])
  const recordEvent = (type: AgentEvent['type'], message: string) => setAgentEvents((events) => [...events, { id: crypto.randomUUID(), type, message, timestamp: Date.now(), stepId: type.startsWith('action_') ? `command-${analysisGeneration.current}` : undefined }].slice(-100))
  const [agentRunState, setAgentRunState] = useState<AgentRunState>('idle')
  const [userRequest, setUserRequest] = useState<string | undefined>(undefined)
  const [parsedIntent, setParsedIntent] = useState<string | undefined>(undefined)
  const [backendStatus, setBackendStatus] = useState<BackendConnectionStatus>('pending')
  const [conversation, setConversation] = useState<ConversationTurn[]>([])
  const runtimeConversation = useRef<RuntimeReply | null>(null)
  const runtimeCancellation = useRef<Promise<void> | null>(null)
  const runtimeBusy = useRef(false)
  const marketDialogue = useRef(false)
  const pendingSearch = useRef<string | undefined>(undefined)
  const navigationPending = useRef(false)
  const navigationController = useRef(new NavigationOrchestrator())
  const [navigationRun, setNavigationRun] = useState<NavigationRunSnapshot | null>(null)
  const appendTurn = (role: 'user' | 'assistant', text: string) => setConversation((turns) => [...turns, { id: crypto.randomUUID(), role, text }].slice(-40))
  const [analysis, setAnalysis] = useState<ShoppingAnalysis | null>(null)
  const [analysisError, setAnalysisError] = useState('')
  const [analysisConsent, setAnalysisConsent] = useState(false)
  const [privacyStatus, setPrivacyStatus] = useState('')
  const analysisGeneration = useRef(0)
  const capturedProducts = useRef(new Map<string, AnalysisProduct>())
  const confirmedBudget = useRef<string | undefined>(undefined)
  const confirmedSpecs = useRef<Record<string, string>>({})
  const activeContext = useRef({ id: activeTabId, url: '' })

  // SAARTHI is adaptive and voice-first: closed by default so webpage gets full width
  const [isSaarthiPanelOpen, setIsSaarthiPanelOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const handleVoiceListening = useCallback((listening: boolean) => setAgentRunState(listening ? 'listening' : 'idle'), [])

  const activeTab = tabs.find((tab) => tab.id === activeTabId) || tabs[0]
  const currentUrl = activeTab?.url || 'https://www.google.com'
  useLayoutEffect(() => { activeContext.current = { id: activeTabId, url: currentUrl } }, [activeTabId, currentUrl])

  const handleStopShopping = () => {
    navigationController.current.cancel()
    setAgentEvents((events) => events.map((event) => event.type === 'action_started' ? { ...event, type: 'action_failed', message: 'Stopped before outcome confirmation' } : event))
    const owned = runtimeConversation.current
    if (owned && (runtimeBusy.current || marketDialogue.current || ['working', 'awaiting_browser', 'ready_to_navigate', 'paused'].includes(owned.status))) {
      const previous = runtimeCancellation.current || Promise.resolve()
      const cancelled = previous.catch(() => {}).then(async () => {
        const latest = await apiClient.getAiConversation(owned.conversation_id)
        const reply = await apiClient.sendAiEvent(owned.conversation_id, { event_id: crypto.randomUUID(), expected_version: latest.version, kind: 'cancel' })
        if (runtimeConversation.current?.conversation_id === reply.conversation_id) runtimeConversation.current = reply
      }).catch(() => { setAnalysisError('Local browser work stopped; backend cancellation could not be confirmed. Check the conversation before starting more work.') })
      runtimeCancellation.current = cancelled
    }
    marketDialogue.current = false
    if (navigationPending.current) recordEvent('action_failed', 'Browser command stopped')
    analysisGeneration.current += 1
    if (navigationPending.current) {
      const target = activeContext.current
      void window.electronAPI?.conversationNavigation({ kind: 'stop', tabId: target.id, expectedUrl: target.url }).catch(() => {})
      navigationPending.current = false
    }
    window.speechSynthesis?.cancel()
    window.electronAPI?.setOrbSpeaking(false)
    setAgentRunState('idle')
  }

  const runNavigation = async (commands: BrowserCommand[], generation: number) => {
    const native = window.electronAPI
    if (!native) { appendTurn('assistant', 'Browser navigation requires the desktop app.'); setAgentRunState('failed'); return }
    navigationPending.current = true
    setParsedIntent('Navigation orchestration')
    try {
      const result = await navigationController.current.start(commands, {
        observe: () => native.getNavigationContext(),
        execute: (command, before, stepId) => native.conversationNavigation({
          ...(command.kind === 'switch' ? { kind: 'switch' as const, targetTabId: command.tabId } : command),
          tabId: before.tabId, expectedUrl: before.url, expectedDocumentId: before.target.document_id, executionId: stepId,
        }),
        stop: () => { const active = activeContext.current; void native.conversationNavigation({ kind: 'stop', tabId: active.id, expectedUrl: active.url }).catch(() => {}) },
        observed: (context) => {
          if (generation !== analysisGeneration.current) return
          setActiveTabId(context.tabId)
          setTabs((previous) => previous.map((tab) => ({ ...tab, isActive: tab.id === context.tabId, ...(tab.id === context.tabId ? { url: context.url } : {}) })))
        },
        changed: (run) => {
          if (generation !== analysisGeneration.current) return
          setNavigationRun(run)
          setAgentRunState(run.status === 'completed' ? 'completed' : run.status === 'failed' ? 'failed' : run.status === 'executing' || run.status === 'planned' ? 'executing' : 'idle')
          setAgentEvents(run.steps.map((step) => ({ id: step.step_id, stepId: step.step_id, timestamp: Date.now(),
            type: step.status === 'executing' ? 'action_started' : step.status === 'completed' ? 'action_completed' : ['unknown', 'failed'].includes(step.status) ? 'action_failed' : 'navigation_planned',
            action: step.command.kind, message: step.command.kind.replaceAll('_', ' ') + ': ' + step.status })))
        },
      })
      if (generation === analysisGeneration.current) appendTurn('assistant', result.message)
    } catch (error) {
      if (generation === analysisGeneration.current) { appendTurn('assistant', error instanceof Error ? error.message : 'Navigation plan failed.'); setAgentRunState('failed') }
    } finally { if (generation === analysisGeneration.current) navigationPending.current = false }
  }

  const handleAskShopping = async (question: string) => {
    if (navigationPending.current || navigationController.current.snapshot()?.status === 'paused') handleStopShopping()
    const generation = ++analysisGeneration.current
    const target = { ...activeContext.current }
    window.speechSynthesis?.cancel()
    setAgentRunState('processing')
    setUserRequest(question)
    setAgentEvents([{ id: crypto.randomUUID(), type: 'agent_started', message: 'Understanding your request', timestamp: Date.now() }])
    setAnalysis(null)
    setNavigationRun(null)
    setAnalysisError('')
    appendTurn('user', question)
    const preferences = updateShoppingPreferences(question, { budget: confirmedBudget.current, specs: confirmedSpecs.current })
    confirmedBudget.current = preferences.budget
    confirmedSpecs.current = preferences.specs
    const marketIntent = /\bacross\s+(websites|stores)|\bcompare prices\b|\bsab websites\b/i.test(question)
    if (marketIntent) marketDialogue.current = true
    const forceRuntime = marketDialogue.current || runtimeConversation.current?.market_result != null && /\b(open|offer|cheapest|excluded|coverage)\b/i.test(question)
    const localSteps = marketIntent ? null : localNavigationPlan(question, tabs, target.id, pendingSearch.current)
    const plan: ReturnType<typeof planConversation> = forceRuntime ? { analyze: true } : planConversation(question, tabs, pendingSearch.current, target.id)
    pendingSearch.current = plan.pendingSearch
    if (localSteps) { pendingSearch.current = undefined; await runNavigation(localSteps, generation); return }
    const compound = /\b(then|phir|aur phir)\b|;|और फिर/i.test(question)
    const navigationGoal = !forceRuntime && (compound || /\b(open|navigate|search|kholo|dhoondo|dikhao|dikhado|take me|browser)\b/i.test(question) && (/\b(amazon|flipkart|google)\b/i.test(question) || plan.analyze))
    if (navigationGoal && /\b(do not|dont|don't|mat|nahi|buy|checkout|pay|payment|delete|login|purchase|add to cart)\b|नहीं|मत/i.test(question)) {
      appendTurn('assistant', 'No browser steps were executed. Negated requests, checkout, payment, login and cart changes are not navigation permissions.')
      setAgentRunState('idle')
      return
    }
    if (navigationGoal) {
      if (!analysisConsent) { appendTurn('assistant', 'Allow analysis in Data and privacy to plan this navigation request. Simple direct browser commands remain available locally.'); setAgentRunState('idle'); return }
      try {
        const context = await window.electronAPI?.getNavigationContext()
        if (generation !== analysisGeneration.current) return
        const host = context ? new URL(context.url).hostname : ''
        const site = /(^|\.)amazon\.in$/.test(host) ? 'amazon' : /(^|\.)flipkart\.com$/.test(host) ? 'flipkart' : /(^|\.)google\.com$/.test(host) ? 'google' : null
        const proposed = await apiClient.planBrowserNavigation(question, tabs.map((tab) => tab.id), site)
        if (generation !== analysisGeneration.current) return
        if (proposed.inference_fallback) appendTurn('assistant', proposed.inference_fallback === 'cloud' ? 'The configured cloud fallback was used for navigation planning.' : 'The navigation model was unavailable; only explicit rule-based planning remains available.')
        if (proposed.status === 'ready') {
          const commands: BrowserCommand[] = proposed.steps.map((step) => {
            if (step.kind === 'navigate' && step.url) return { kind: 'navigate', url: step.url }
            if (step.kind === 'switch' && step.tab_id) return { kind: 'switch', tabId: step.tab_id }
            if (step.kind === 'open_result' && step.index) return { kind: 'open_result', index: step.index }
            if (['back', 'forward', 'reload', 'scroll_down', 'scroll_up'].includes(step.kind)) return { kind: step.kind } as BrowserCommand
            throw new Error('Navigation planner returned an unsupported step.')
          })
          pendingSearch.current = undefined
          await runNavigation(commands, generation)
          return
        }
        if (proposed.status === 'needs_input') { appendTurn('assistant', 'Please specify the website and the browser steps you want. No part of this request was executed. Checkout, payments, login and cart actions are disabled.'); setAgentRunState('idle'); return }
      } catch (error) { if (generation === analysisGeneration.current) { appendTurn('assistant', error instanceof Error ? error.message : 'Navigation planning is unavailable.'); setAgentRunState('failed') }; return }
    }
    if (!plan.analyze) {
      appendTurn('assistant', plan.reply || 'Please clarify your browser request.')
      setAgentRunState('completed')
      return
    }
    let requestedBackend = false
    let localCapture: AnalysisProduct | null = null
    try {
      if (!analysisConsent) {
        appendTurn('assistant', 'Please read Data and privacy and allow analysis before sending questions or captured product details to the backend. Browser commands still work without this permission.')
        recordEvent('agent_completed', 'Waiting for analysis permission')
        setAgentRunState('idle')
        return
      }
      if (!window.electronAPI) throw new Error('Open this assistant in the desktop browser to read a product page.')
      const captured = forceRuntime ? { success: false, snapshot: undefined } : await window.electronAPI.extractPageSnapshot(target.id)
      if (generation !== analysisGeneration.current || activeContext.current.id !== target.id || activeContext.current.url !== target.url) return
      if (!captured.success || !captured.snapshot) {
        if (!forceRuntime && isSupportedProductAddress(target.url)) throw new Error('This product page could not be read. Refresh it and retry, or use Report a page-reading problem. No product answer was generated.')
        requestedBackend = true
        await runtimeCancellation.current
        if (generation !== analysisGeneration.current) return
        let current = runtimeConversation.current
        if (!current) {
          const session = await apiClient.createSession()
          if (generation !== analysisGeneration.current) return
          current = await apiClient.startAiConversation(session.id)
          if (generation !== analysisGeneration.current) return
          runtimeConversation.current = current
        }
        // Only the user's words enter dialogue; page text cannot become commands.
        const browserContext = await window.electronAPI.getNavigationContext()
        if (generation !== analysisGeneration.current) return
        const capabilities = await apiClient.getAiCapabilities()
        if (generation !== analysisGeneration.current) return
        const contextAllowed = capabilities.browser_origins.includes(new URL(browserContext.url).origin)
        runtimeBusy.current = true
        let reply: RuntimeReply
        try {
          reply = await apiClient.sendAiEvent(current.conversation_id, {
            event_id: crypto.randomUUID(), expected_version: current.version, kind: 'text', message: question,
            ...(contextAllowed ? { target: browserContext.target, origin: new URL(browserContext.url).origin } : {}),
          })
        } finally { runtimeBusy.current = false }
        if (runtimeConversation.current?.conversation_id === reply.conversation_id) runtimeConversation.current = reply
        if (generation !== analysisGeneration.current) return
        let workNote: string | undefined
        if (reply.work_ids.length) {
          navigationPending.current = true
          try {
            const completed = await drainRuntimeNavigation(apiClient, reply, {
              observe: () => window.electronAPI!.getNavigationContext(),
              capture: (tabId) => window.electronAPI!.extractPageSnapshot(tabId),
              searchOffers: (tabId) => window.electronAPI!.extractSearchOffers(tabId),
              navigate: (url, before, stepId) => window.electronAPI!.conversationNavigation({ kind: 'navigate', url,
                tabId: before.tabId, expectedUrl: before.url, expectedDocumentId: before.target.document_id, executionId: stepId }),
              alive: () => generation === analysisGeneration.current,
              progress: (message, dispatchId, status) => {
                if (generation !== analysisGeneration.current) return
                setAgentRunState('executing')
                setAgentEvents((events) => [...events, { id: crypto.randomUUID(), stepId: dispatchId, timestamp: Date.now(), message,
                  type: status === 'executing' ? 'action_started' : status === 'completed' ? 'action_completed' : 'action_failed' }].slice(-100))
              },
            })
            reply = completed.reply; workNote = completed.note
            if (runtimeConversation.current?.conversation_id === reply.conversation_id) runtimeConversation.current = reply
          } finally { if (generation === analysisGeneration.current) navigationPending.current = false }
        }
        if (generation !== analysisGeneration.current) return
        appendTurn('assistant', [reply.message, workNote].filter(Boolean).join(' '))
        setBackendStatus('connected')
        const pendingWork = reply.status === 'paused' || reply.work_ids.length > 0
        recordEvent(pendingWork ? 'agent_paused' : 'agent_completed', pendingWork ? 'Waiting for verified browser work' : 'Conversation reply ready')
        setAgentRunState(pendingWork ? 'idle' : reply.status === 'error' ? 'failed' : 'completed')
        return
      }
      const source = captured.snapshot
      const id = `${source.retailer}:${source.retailer_product_id}`.slice(0, 100)
      const knownPrice = /^\d{1,12}(?:\.\d{1,2})?$/.test(source.price) && Number(source.price) > 0 && /^[A-Z]{3}$/.test(source.currency)
      const product: AnalysisProduct = {
        source_url: source.url, reviews: source.reviews || [],
        product: {
          product_id: id, snapshot_id: crypto.randomUUID(), document_id: source.document_id,
          title: source.title, observed_at: source.captured_at,
          price: knownPrice ? source.price : null, currency: knownPrice ? source.currency : null,
          in_stock: source.availability === 'in_stock' ? true : source.availability === 'out_of_stock' ? false : null,
          selected_variant_id: null, variants: [], specs: source.specs || {},
        },
      }
      capturedProducts.current.set(id, product)
      localCapture = product
      while (capturedProducts.current.size > 4) capturedProducts.current.delete(capturedProducts.current.keys().next().value!)
      const candidateBudget = preferences.budget
      const requirements = preferences.specs
      requestedBackend = true
      const result = await apiClient.analyzeShopping(question, [...capturedProducts.current.values()], { selected_product_id: id, budget: candidateBudget, required_specs: requirements })
      if (generation !== analysisGeneration.current || activeContext.current.id !== target.id || activeContext.current.url !== target.url) return
      setAnalysis(result)
      appendTurn('assistant', [result.answer, result.follow_up].filter(Boolean).join(' '))
      if (result.status !== 'needs_input') { confirmedSpecs.current = requirements; confirmedBudget.current = candidateBudget }
      recordEvent('agent_completed', 'Product evidence answer ready')
      setParsedIntent('Product evidence analysis')
      setBackendStatus('connected')
      setAgentRunState('completed')
    } catch (error) {
      if (generation !== analysisGeneration.current) return
      if (error instanceof NetworkError && localCapture) setAnalysis(rawCaptureFallback(localCapture))
      const message = error instanceof Error ? error.message : 'Shopping analysis is unavailable. Please retry.'
      setAnalysisError(message)
      appendTurn('assistant', message)
      recordEvent('action_failed', 'The request could not be completed')
      if (runtimeConversation.current) {
        const id = runtimeConversation.current.conversation_id
        void apiClient.getAiConversation(id).then((reply) => { if (runtimeConversation.current?.conversation_id === id) runtimeConversation.current = reply }).catch(() => {})
      }
      if (requestedBackend) setBackendStatus(error instanceof NetworkError ? 'disconnected' : 'connected')
      setAgentRunState('failed')
    }
  }
  const isRefreshing = activeTab?.isLoading ?? false
  const canGoBack = activeTab?.canGoBack ?? false
  const canGoForward = activeTab?.canGoForward ?? false

  useEffect(() => {
    if (!window.electronAPI) return
    const electronAPI = window.electronAPI

    const unsubUrl = electronAPI.onPageUrlChange(({ tabId, url }) => {
      if (tabId === activeContext.current.id && url !== activeContext.current.url && !navigationPending.current) {
        analysisGeneration.current += 1
        setAnalysis(null)
        setAgentRunState('idle')
        window.speechSynthesis?.cancel()
      }
      setTabs((previous) => previous.map((tab) => (tab.id === tabId ? { ...tab, url } : tab)))
    })
    const unsubTitle = electronAPI.onPageTitleChange(({ tabId, title }) => {
      setTabs((previous) => previous.map((tab) => (tab.id === tabId ? { ...tab, title } : tab)))
    })
    const unsubLoading = electronAPI.onPageLoading(({ tabId, isLoading }) => {
      setTabs((previous) => previous.map((tab) => (tab.id === tabId ? { ...tab, isLoading } : tab)))
    })
    const unsubNavigation = electronAPI.onNavigationState(({ tabId, canGoBack, canGoForward }) => {
      setTabs((previous) => previous.map((tab) => (
        tab.id === tabId ? { ...tab, canGoBack, canGoForward } : tab
      )))
    })
    const unsubNewTab = electronAPI.onNewTabRequested(({ url }) => {
      analysisGeneration.current += 1
      setAnalysis(null)
      setAgentRunState('idle')
      window.speechSynthesis?.cancel()
      const tabId = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const newTab: BrowserTab = { id: tabId, title: 'New Tab', url, isActive: true }
      setTabs((previous) => [...previous.map((tab) => ({ ...tab, isActive: false })), newTab])
      setActiveTabId(tabId)
      setSearchQuery('')
      electronAPI.createTab(tabId, url)
    })
    for (const tab of INITIAL_TABS.slice(1)) electronAPI.createTab(tab.id, tab.url, false)

    return () => {
      unsubUrl()
      unsubTitle()
      unsubLoading()
      unsubNavigation()
      unsubNewTab()
    }
  }, [])

  const handleSelectTab = (tabId: string) => {
    if (tabId === activeTabId) return
    handleStopShopping()
    setAnalysis(null)
    window.electronAPI?.switchTab(tabId)
    setActiveTabId(tabId)
    setTabs((previous) => previous.map((tab) => ({ ...tab, isActive: tab.id === tabId })))
  }

  const handleCloseTab = (tabId: string, event: React.MouseEvent) => {
    event.stopPropagation()
    if (tabs.length <= 1) return
    if (tabId === activeTabId) handleStopShopping()

    const tabIndex = tabs.findIndex((tab) => tab.id === tabId)
    const remainingTabs = tabs.filter((tab) => tab.id !== tabId)
    const nextActive = tabId === activeTabId ? remainingTabs[Math.max(0, tabIndex - 1)] : undefined

    if (nextActive) {
      window.electronAPI?.switchTab(nextActive.id)
      setActiveTabId(nextActive.id)
    }
    window.electronAPI?.closeTab(tabId)
    setTabs(remainingTabs.map((tab) => ({ ...tab, isActive: tab.id === (nextActive?.id || activeTabId) })))
  }

  const handleNewTab = () => {
    handleStopShopping()
    setAnalysis(null)
    const tabId = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const newTab: BrowserTab = { id: tabId, title: 'Google', url: 'https://www.google.com', isActive: true }
    setTabs((previous) => [...previous.map((tab) => ({ ...tab, isActive: false })), newTab])
    setActiveTabId(tabId)
    setSearchQuery('')
    window.electronAPI?.createTab(tabId, newTab.url)
  }

  const handleNavigate = (url: string) => {
    handleStopShopping()
    setAnalysis(null)
    if (window.electronAPI) {
      window.electronAPI.navigate(url, activeTabId)
      return
    }
    setTabs((previous) => previous.map((tab) => tab.id === activeTabId ? { ...tab, url, isLoading: true } : tab))
    window.setTimeout(() => {
      setTabs((previous) => previous.map((tab) => {
        if (tab.id !== activeTabId) return tab
        try {
          return { ...tab, url, title: new URL(url).hostname, isLoading: false }
        } catch {
          return { ...tab, url, title: url, isLoading: false }
        }
      }))
    }, 350)
  }

  const handleSearch = (query: string) => {
    setSearchQuery(query)
    handleNavigate(`https://www.google.com/search?q=${encodeURIComponent(query)}`)
  }

  // The avatar opens the shared browser conversation panel.
  const handleActivateAssistant = useCallback(() => {
    setIsSaarthiPanelOpen(true)
  }, [])

  useEffect(() => {
    if (!window.electronAPI) return
    return window.electronAPI.onOrbActivated(handleActivateAssistant)
  }, [handleActivateAssistant])

  const handleIdleOrbBoundsChange = useCallback((bounds: OrbOverlayBounds | null) => {
    window.electronAPI?.updateOrbOverlay(bounds)
  }, [])

  useEffect(() => {
    window.electronAPI?.updateOrbOverlayState(agentRunState, isSaarthiPanelOpen)
  }, [agentRunState, isSaarthiPanelOpen])

  const handleClosePanel = () => {
    handleStopShopping()
    setIsSaarthiPanelOpen(false)
    setAgentRunState('idle')
  }

  const handleClearExecution = () => {
    handleStopShopping()
    runtimeConversation.current = null
    setNavigationRun(null)
    setConversation([])
    pendingSearch.current = undefined
    capturedProducts.current.clear()
    confirmedBudget.current = undefined
    confirmedSpecs.current = {}
    setAnalysis(null)
    setAnalysisError('')
    setAgentEvents([])
    setUserRequest(undefined)
    setParsedIntent(undefined)
    setAgentRunState('idle')
  }

  const handleAnalysisConsent = (allowed: boolean) => {
    handleClearExecution()
    setAnalysisConsent(allowed)
    setPrivacyStatus(allowed ? 'Analysis allowed for this app session. You can withdraw it here.' : 'Analysis stopped. Existing server records are removed only by Delete stored shopping data.')
  }
  const handleCheckUsage = async () => {
    if (!analysisConsent) { setPrivacyStatus('Allow analysis before connecting to the backend.'); return }
    const generation = analysisGeneration.current
    try {
      const usage = await apiClient.getOwnUsage()
      if (generation !== analysisGeneration.current) return
      setPrivacyStatus(`Today: ${usage.ai_requests}/${usage.ai_limit} AI turns; ${usage.ai_remaining} remaining. Resets ${new Date(usage.resets_at).toLocaleString()}. Provider: ${usage.processing_provider}${usage.cloud_fallback_enabled ? ' with cloud fallback' : ''}. Counts measure admitted turns, not provider calls, tokens or billed cost.`)
    } catch (error) { if (generation === analysisGeneration.current) setPrivacyStatus(error instanceof Error ? error.message : 'Usage is unavailable.') }
  }
  const handleEraseStoredData = async () => {
    handleClearExecution()
    setAnalysisConsent(false)
    setPrivacyStatus('Deleting stored shopping data…')
    try {
      await apiClient.eraseShoppingData()
      setPrivacyStatus('Stored shopping sessions, checkpoints, snapshots, price observations, alerts and owned review summaries deleted from the active database. Account identity, authentication and usage limits remain. Backups and external provider records follow their retention policies.')
    } catch (error) { setPrivacyStatus(error instanceof Error ? error.message : 'Deletion failed. Stored data may remain; retry.') }
  }

  // Developer verification hook to test UI failure state handling
  const handleSimulateAgentFailure = () => {
    const timestamp = Date.now()
    setIsSaarthiPanelOpen(true)
    setUserRequest('Demonstration failure test')
    setParsedIntent('Simulate agent failure handling for UI validation')
    setAgentRunState('executing')
    setAgentEvents([
      {
        id: `test-started-${timestamp}`,
        type: 'agent_started',
        message: 'Initializing test execution',
        timestamp,
      },
      {
        id: `test-nav-${timestamp}`,
        type: 'navigation',
        action: 'navigate',
        stepId: 'nav-test',
        message: 'Target action simulation',
        timestamp: timestamp + 200,
      },
      {
        id: `test-failed-${timestamp}`,
        type: 'action_failed',
        action: 'navigate',
        stepId: 'nav-test',
        message: 'Simulated connection failure (Test Verification)',
        timestamp: timestamp + 500,
      },
    ])
    setAgentRunState('failed')
  }

  return (
    <BrowserShell
      navigationRun={navigationRun}
      onPauseNavigation={() => navigationController.current.pause()}
      onResumeNavigation={async () => {
        navigationPending.current = true
        try { const run = await navigationController.current.resume(); if (run) appendTurn('assistant', run.message) }
        finally { navigationPending.current = false }
      }}
      analysisConsent={analysisConsent}
      onAnalysisConsent={handleAnalysisConsent}
      onEraseStoredData={handleEraseStoredData}
      onCheckUsage={handleCheckUsage}
      privacyStatus={privacyStatus}
      diagnosticReport={buildReadDiagnostic(currentUrl, analysisError ? 'read_failed' : 'check_page')}
      conversation={conversation}
      onVoiceListening={handleVoiceListening}
      analysis={analysis}
      analysisError={analysisError}
      onAskShopping={handleAskShopping}
      onStopShopping={handleStopShopping}
      tabs={tabs}
      activeTabId={activeTabId}
      onSelectTab={handleSelectTab}
      onCloseTab={handleCloseTab}
      onNewTab={handleNewTab}
      currentUrl={currentUrl}
      onNavigate={handleNavigate}
      onRefresh={() => window.electronAPI?.reload(activeTabId)}
      onGoBack={() => window.electronAPI?.goBack(activeTabId)}
      onGoForward={() => window.electronAPI?.goForward(activeTabId)}
      canGoBack={canGoBack}
      canGoForward={canGoForward}
      isRefreshing={isRefreshing}
      agentEvents={agentEvents}
      agentRunState={agentRunState}
      onActivateAssistant={handleActivateAssistant}
      backendStatus={backendStatus}
      onSimulateAgentFailure={import.meta.env.DEV ? handleSimulateAgentFailure : undefined}
      onClearExecution={handleClearExecution}
      isSaarthiPanelOpen={isSaarthiPanelOpen}
      onToggleSaarthiPanel={() => { if (isSaarthiPanelOpen) handleStopShopping(); setIsSaarthiPanelOpen((open) => !open) }}
      onCloseSaarthiPanel={handleClosePanel}
      searchQuery={searchQuery}
      onSearch={handleSearch}
      userRequest={userRequest}
      parsedIntent={parsedIntent}
      onIdleOrbBoundsChange={handleIdleOrbBoundsChange}
    />
  )
}

export default App
