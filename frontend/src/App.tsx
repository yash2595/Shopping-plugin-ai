import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { BrowserShell } from './components/browser/BrowserShell'
import { INITIAL_TABS } from './data/mockBrowserData'
import type { AgentEvent, AgentRunState } from './types/agentEvents'
import type { BackendConnectionStatus } from './types/assistant'
import type { BrowserTab } from './types/browser'
import type { OrbOverlayBounds } from './types/electron'
import type { AnalysisProduct, ShoppingAnalysis } from './types/assistant'
import { apiClient, NetworkError } from './services/apiClient'
import { planConversation, updateShoppingPreferences } from './utils/browserConversation'
import type { RuntimeReply } from './types/assistant'
import type { ConversationTurn } from './types/assistant'
import { rawCaptureFallback } from './utils/snapshotExtractor'

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
  const pendingSearch = useRef<string | undefined>(undefined)
  const navigationPending = useRef(false)
  const appendTurn = (role: 'user' | 'assistant', text: string) => setConversation((turns) => [...turns, { id: crypto.randomUUID(), role, text }].slice(-40))
  const [analysis, setAnalysis] = useState<ShoppingAnalysis | null>(null)
  const [analysisError, setAnalysisError] = useState('')
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

  const handleAskShopping = async (question: string) => {
    if (navigationPending.current) handleStopShopping()
    const generation = ++analysisGeneration.current
    const target = { ...activeContext.current }
    window.speechSynthesis?.cancel()
    setAgentRunState('processing')
    setUserRequest(question)
    setAgentEvents([{ id: crypto.randomUUID(), type: 'agent_started', message: 'Understanding your request', timestamp: Date.now() }])
    setAnalysis(null)
    setAnalysisError('')
    appendTurn('user', question)
    const preferences = updateShoppingPreferences(question, { budget: confirmedBudget.current, specs: confirmedSpecs.current })
    confirmedBudget.current = preferences.budget
    confirmedSpecs.current = preferences.specs
    const plan = planConversation(question, tabs, pendingSearch.current, target.id)
    pendingSearch.current = plan.pendingSearch
    if (!plan.analyze) {
      if (plan.reply) { appendTurn('assistant', plan.reply); recordEvent('agent_completed', 'Conversation reply ready'); setAgentRunState('completed'); return }
      if (plan.command) {
        const command = plan.command
        recordEvent('action_started', 'Checking the requested browser command')
        if (!window.electronAPI) { appendTurn('assistant', 'Browser navigation is available in the desktop app.'); recordEvent('action_failed', 'Desktop browser unavailable'); setAgentRunState('failed'); return }
        if (command.kind === 'switch') {
          try {
            const result = await window.electronAPI.conversationNavigation({ kind: 'switch', tabId: target.id, expectedUrl: target.url, targetTabId: command.tabId })
            if (generation !== analysisGeneration.current) return
            if (result.success) { setActiveTabId(command.tabId); setTabs((previous) => previous.map((tab) => ({ ...tab, isActive: tab.id === command.tabId }))) }
            appendTurn('assistant', result.message); recordEvent(result.success ? 'action_completed' : 'action_failed', result.message); setAgentRunState(result.success ? 'completed' : 'failed')
          } catch { if (generation === analysisGeneration.current) { appendTurn('assistant', 'The tab could not be switched.'); recordEvent('action_failed', 'Tab switch failed'); setAgentRunState('failed') } }
          return
        }
        navigationPending.current = true
        setParsedIntent('Browser navigation')
        setAgentRunState('executing')
        try {
          const result = await window.electronAPI.conversationNavigation({ ...command, tabId: target.id, expectedUrl: target.url })
          if (generation !== analysisGeneration.current || activeContext.current.id !== target.id) return
          appendTurn('assistant', result.message)
          recordEvent(result.success ? 'action_completed' : 'action_failed', result.message)
          setAgentRunState(result.success ? 'completed' : 'failed')
        } catch { if (generation === analysisGeneration.current) { appendTurn('assistant', 'Browser navigation is unavailable. Please retry.'); recordEvent('action_failed', 'Browser navigation unavailable'); setAgentRunState('failed') } }
        finally { if (generation === analysisGeneration.current) navigationPending.current = false }
        return
      }
    }
    let requestedBackend = false
    let localCapture: AnalysisProduct | null = null
    try {
      if (!window.electronAPI) throw new Error('Open this assistant in the desktop browser to read a product page.')
      const captured = await window.electronAPI.extractPageSnapshot(target.id)
      if (generation !== analysisGeneration.current || activeContext.current.id !== target.id || activeContext.current.url !== target.url) return
      if (!captured.success || !captured.snapshot) {
        requestedBackend = true
        let current = runtimeConversation.current
        if (!current) {
          const session = await apiClient.createSession()
          current = await apiClient.startAiConversation(session.id)
          if (generation !== analysisGeneration.current) return
          runtimeConversation.current = current
        }
        // Only the user's words enter dialogue; page text cannot become commands.
        const reply = await apiClient.sendAiEvent(current.conversation_id, {
          event_id: crypto.randomUUID(), expected_version: current.version, kind: 'text', message: question,
        })
        if (runtimeConversation.current?.conversation_id === reply.conversation_id) runtimeConversation.current = reply
        if (generation !== analysisGeneration.current) return
        appendTurn('assistant', reply.work_ids.length ? 'I have your shopping request. To navigate, give an explicit command such as “search headphones on Amazon”. I have not opened or clicked anything.' : reply.message)
        setBackendStatus('connected')
        recordEvent('agent_completed', 'Conversation reply ready')
        setAgentRunState('completed')
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
