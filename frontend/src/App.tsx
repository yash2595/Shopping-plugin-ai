import { useCallback, useEffect, useState } from 'react'
import { BrowserShell } from './components/browser/BrowserShell'
import { INITIAL_TABS } from './data/mockBrowserData'
import type { AgentEvent, AgentRunState } from './types/agentEvents'
import type { BackendConnectionStatus } from './types/assistant'
import type { BrowserTab } from './types/browser'
import type { OrbOverlayBounds } from './types/electron'

function App() {
  const [tabs, setTabs] = useState<BrowserTab[]>(INITIAL_TABS)
  const [activeTabId, setActiveTabId] = useState<string>(INITIAL_TABS[0].id)
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([])
  const [agentRunState, setAgentRunState] = useState<AgentRunState>('idle')
  const [userRequest, setUserRequest] = useState<string | undefined>(undefined)
  const [parsedIntent, setParsedIntent] = useState<string | undefined>(undefined)
  const [backendStatus] = useState<BackendConnectionStatus>('pending')

  // SAARTHI is adaptive and voice-first: closed by default so webpage gets full width
  const [isSaarthiPanelOpen, setIsSaarthiPanelOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const activeTab = tabs.find((tab) => tab.id === activeTabId) || tabs[0]
  const currentUrl = activeTab?.url || 'https://www.google.com'
  const isRefreshing = activeTab?.isLoading ?? false
  const canGoBack = activeTab?.canGoBack ?? false
  const canGoForward = activeTab?.canGoForward ?? false

  useEffect(() => {
    if (!window.electronAPI) return
    const electronAPI = window.electronAPI

    const unsubUrl = electronAPI.onPageUrlChange(({ tabId, url }) => {
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
    window.electronAPI?.switchTab(tabId)
    setActiveTabId(tabId)
    setTabs((previous) => previous.map((tab) => ({ ...tab, isActive: tab.id === tabId })))
  }

  const handleCloseTab = (tabId: string, event: React.MouseEvent) => {
    event.stopPropagation()
    if (tabs.length <= 1) return

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
    const tabId = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const newTab: BrowserTab = { id: tabId, title: 'Google', url: 'https://www.google.com', isActive: true }
    setTabs((previous) => [...previous.map((tab) => ({ ...tab, isActive: false })), newTab])
    setActiveTabId(tabId)
    setSearchQuery('')
    window.electronAPI?.createTab(tabId, newTab.url)
  }

  const handleNavigate = (url: string) => {
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

  // Clean assistant activation: toggles panel and prepares for future live voice/AI input
  const handleActivateAssistant = useCallback(() => {
    setIsSaarthiPanelOpen((open) => !open)
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
    setIsSaarthiPanelOpen(false)
    setAgentRunState('idle')
  }

  const handleClearExecution = () => {
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
      onToggleSaarthiPanel={() => setIsSaarthiPanelOpen((open) => !open)}
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
