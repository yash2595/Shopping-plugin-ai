import React, { useState, useEffect } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  RotateCw,
  Lock,
  Plus,
  X,
  Globe,
  Star,
  PanelRight,
  ShieldCheck,
} from 'lucide-react'
import type { BrowserTab } from '../../types/browser'

interface BrowserToolbarProps {
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
  isRefreshing?: boolean
  isSaarthiPanelOpen: boolean
  onToggleSaarthiPanel: () => void
}

export const BrowserToolbar: React.FC<BrowserToolbarProps> = ({
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
  canGoBack = false,
  canGoForward = false,
  isRefreshing = false,
  isSaarthiPanelOpen,
  onToggleSaarthiPanel,
}) => {
  const [urlInput, setUrlInput] = useState(currentUrl)

  useEffect(() => {
    setUrlInput(currentUrl)
  }, [currentUrl])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      let finalUrl = urlInput.trim()
      if (!finalUrl.startsWith('http://') && !finalUrl.startsWith('https://')) {
        if (finalUrl.includes('.')) {
          finalUrl = 'https://' + finalUrl
        } else {
          finalUrl = `https://www.google.com/search?q=${encodeURIComponent(finalUrl)}`
        }
      }
      onNavigate(finalUrl)
    }
  }

  return (
    <header className="flex flex-col bg-neutral-900 border-b border-neutral-800/80 select-none text-xs text-neutral-300">
      {/* Top Window Strip: Window controls & Tabs */}
      <div className="flex items-center h-10 px-3 bg-neutral-950/60 gap-3 border-b border-neutral-900">
        {/* macOS / Desktop Window Dots */}
        <div className="flex items-center gap-2 pr-2">
          <div className="w-3 h-3 rounded-full bg-rose-500/80 hover:bg-rose-500 cursor-pointer transition-colors shadow-xs" />
          <div className="w-3 h-3 rounded-full bg-amber-500/80 hover:bg-amber-500 cursor-pointer transition-colors shadow-xs" />
          <div className="w-3 h-3 rounded-full bg-emerald-500/80 hover:bg-emerald-500 cursor-pointer transition-colors shadow-xs" />
        </div>

        {/* Tab Strip */}
        <div className="flex items-center gap-1 flex-1 overflow-x-auto no-scrollbar max-w-4xl py-1">
          {tabs.map((tab) => {
            const isActive = tab.id === activeTabId
            return (
              <div
                key={tab.id}
                onClick={() => onSelectTab(tab.id)}
                className={`group relative flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs cursor-pointer max-w-[220px] transition-all border ${
                  isActive
                    ? 'bg-neutral-800 text-white font-medium border-neutral-700/60 shadow-xs'
                    : 'text-neutral-400 hover:bg-neutral-800/40 hover:text-neutral-200 border-transparent'
                }`}
              >
                <Globe className={`w-3.5 h-3.5 shrink-0 ${isActive ? 'text-indigo-400' : 'text-neutral-500'}`} />
                <span className="truncate flex-1 tracking-tight text-[11px]">{tab.title}</span>
                {tabs.length > 1 && (
                  <button
                    type="button"
                    onClick={(e) => onCloseTab(tab.id, e)}
                    className="opacity-0 group-hover:opacity-100 hover:bg-neutral-700 p-0.5 rounded text-neutral-400 hover:text-white transition-opacity"
                    title="Close tab"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
            )
          })}

          {/* New Tab Button */}
          <button
            type="button"
            onClick={onNewTab}
            className="p-1 rounded-md text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 transition-colors"
            title="Open new tab"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Browser Brand Tag */}
        <div className="ml-auto flex items-center gap-2 text-[11px] font-medium text-neutral-400">
          <span className="px-2 py-0.5 rounded bg-neutral-800 text-neutral-300 font-mono text-[10px] border border-neutral-700">
            COPILOT BROWSER
          </span>
        </div>
      </div>

      {/* Navigation Controls & Omnibox URL Bar */}
      <div className="flex items-center gap-2 px-3 py-2 bg-neutral-900">
        {/* Navigation History & Refresh */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onGoBack}
            disabled={!canGoBack}
            className="p-1.5 rounded-md text-neutral-400 hover:text-white hover:bg-neutral-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
            title="Back"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={onGoForward}
            disabled={!canGoForward}
            className="p-1.5 rounded-md text-neutral-400 hover:text-white hover:bg-neutral-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
            title="Forward"
          >
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={onRefresh}
            className={`p-1.5 rounded-md text-neutral-400 hover:text-white hover:bg-neutral-800 transition-colors ${
              isRefreshing ? 'animate-spin text-indigo-400' : ''
            }`}
            title="Reload current page"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Address / Search Bar */}
        <div className="flex-1 relative flex items-center">
          <div className="w-full flex items-center bg-neutral-950/80 border border-neutral-800 focus-within:border-indigo-500/80 focus-within:ring-1 focus-within:ring-indigo-500/30 rounded-lg px-3 py-1.5 gap-2 transition-all">
            <div className="flex items-center gap-1.5 text-neutral-400 shrink-0">
              <Lock className="w-3 h-3 text-emerald-400" />
              <span className="text-[10px] text-emerald-400/90 font-mono hidden sm:inline">Secure</span>
            </div>

            <input
              type="text"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search or enter web address..."
              className="w-full bg-transparent text-neutral-200 text-xs focus:outline-none tracking-normal font-sans"
            />

            <div className="flex items-center gap-1.5 shrink-0 text-neutral-400">
              <button
                type="button"
                className="hover:text-amber-400 transition-colors p-0.5 rounded"
                title="Bookmark page"
              >
                <Star className="w-3.5 h-3.5" />
              </button>
              <div className="h-3 w-px bg-neutral-800" />
              <div className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-300 text-[10px] border border-indigo-500/20 font-medium">
                <ShieldCheck className="w-3 h-3 text-indigo-400" />
                <span className="hidden md:inline">Agent Sandboxed</span>
              </div>
            </div>
          </div>
        </div>

        {/* Right Action Icons & Saarthi Panel Toggle */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onToggleSaarthiPanel}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border transition-all ${
              isSaarthiPanelOpen
                ? 'bg-indigo-600/15 border-indigo-500/50 text-indigo-300 font-medium'
                : 'bg-neutral-800/80 border-neutral-700 text-neutral-400 hover:text-white hover:bg-neutral-800'
            }`}
            title="Toggle Copilot"
          >
            <PanelRight className="w-3.5 h-3.5" />
            <span className="text-[11px] font-medium hidden sm:inline">Copilot</span>
          </button>
        </div>
      </div>
    </header>
  )
}
