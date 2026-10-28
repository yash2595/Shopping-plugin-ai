import React from 'react'
import { BrowserToolbar } from './BrowserToolbar'
import { BrowserSidebar } from './BrowserSidebar'
import { BrowserViewport } from './BrowserViewport'
import { SaarthiPanel } from '../saarthi/SaarthiPanel'
import { SaarthiAvatar } from '../saarthi/SaarthiAvatar'
import type { BrowserTab } from '../../types/browser'
import type { AgentEvent, AgentRunState } from '../../types/agentEvents'
import type { BackendConnectionStatus, ShoppingPanelIntegration } from '../../types/assistant'
import type { OrbOverlayBounds } from '../../types/electron'

interface BrowserShellProps extends ShoppingPanelIntegration {
  tabs: BrowserTab[]
  activeTabId: string
  onSelectTab: (tabId: string) => void
  onCloseTab: (tabId: string, e: React.MouseEvent) => void
  onNewTab: () => void
  currentUrl: string
  onNavigate: (newUrl: string) => void
  onRefresh: () => void
  onGoBack?: () => void
  onGoForward?: () => void
  canGoBack?: boolean
  canGoForward?: boolean
  isRefreshing: boolean
  agentEvents: AgentEvent[]
  agentRunState: AgentRunState
  onActivateAssistant: () => void
  backendStatus?: BackendConnectionStatus
  onSimulateAgentFailure?: () => void
  onClearExecution: () => void
  isSaarthiPanelOpen: boolean
  onToggleSaarthiPanel: () => void
  onCloseSaarthiPanel?: () => void
  searchQuery?: string
  onSearch?: (query: string) => void
  userRequest?: string
  parsedIntent?: string
  onIdleOrbBoundsChange?: (bounds: OrbOverlayBounds | null) => void
}

export const BrowserShell: React.FC<BrowserShellProps> = ({
  tabs,
  activeTabId,
  onSelectTab,
  onCloseTab,
  onNewTab,
  currentUrl,
  onNavigate,
  onRefresh,
  onGoBack,
  onGoForward,
  canGoBack,
  canGoForward,
  isRefreshing,
  agentEvents,
  agentRunState,
  onActivateAssistant,
  backendStatus = 'pending',
  onSimulateAgentFailure,
  onClearExecution,
  isSaarthiPanelOpen,
  onToggleSaarthiPanel,
  onCloseSaarthiPanel,
  searchQuery,
  onSearch,
  userRequest,
  parsedIntent,
  onIdleOrbBoundsChange,
  conversation, onVoiceListening, analysis, analysisError, onAskShopping, onStopShopping,
}) => {
  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0]

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-neutral-950 select-none antialiased">
      {/* Browser Chrome Toolbar & Tabs */}
      <BrowserToolbar
        tabs={tabs}
        activeTabId={activeTabId}
        onSelectTab={onSelectTab}
        onCloseTab={onCloseTab}
        onNewTab={onNewTab}
        currentUrl={currentUrl}
        onNavigate={onNavigate}
        onRefresh={onRefresh}
        onGoBack={onGoBack}
        onGoForward={onGoForward}
        canGoBack={canGoBack}
        canGoForward={canGoForward}
        isRefreshing={isRefreshing}
        isSaarthiPanelOpen={isSaarthiPanelOpen}
        onToggleSaarthiPanel={onToggleSaarthiPanel}
      />

      {/* Main Browser Window Body: Left Sidebar + Viewport + Right Saarthi Panel */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* Left Browser Sidebar */}
        <BrowserSidebar />

        {/* Central Webpage Viewport */}
        <div className="flex-1 h-full overflow-hidden flex flex-col relative bg-white">
          <BrowserViewport
            currentUrl={currentUrl}
            activeTabTitle={activeTab ? activeTab.title : 'New Tab'}
            layoutVersion={isSaarthiPanelOpen}
            searchQuery={searchQuery}
            onSearch={onSearch}
          />

          {/* SAARTHI Adaptive Voice Orb (Idle at bottom-right or Centered Listening/Processing overlay) */}
          <SaarthiAvatar
            runState={agentRunState}
            isPanelOpen={isSaarthiPanelOpen}
            onActivate={onActivateAssistant}
            onBoundsChange={onIdleOrbBoundsChange}
          />
        </div>

        {/* Right SAARTHI Execution Panel (slides in dynamically when executing, shrinks WebContentsView) */}
        {isSaarthiPanelOpen && (
          <SaarthiPanel
            analysis={analysis}
            conversation={conversation}
            onVoiceListening={onVoiceListening}
            analysisError={analysisError}
            onAskShopping={onAskShopping}
            onStopShopping={onStopShopping}
            events={agentEvents}
            runState={agentRunState}
            backendStatus={backendStatus}
            onSimulateFailure={onSimulateAgentFailure}
            onClearExecution={onClearExecution}
            onTogglePanel={onToggleSaarthiPanel}
            onClosePanel={onCloseSaarthiPanel}
            activeTabTitle={activeTab ? activeTab.title : 'Active Tab'}
            userRequest={userRequest}
            parsedIntent={parsedIntent}
          />
        )}
      </div>
    </div>
  )
}
