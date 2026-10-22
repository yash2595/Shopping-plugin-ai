import React, { useState, useEffect, useLayoutEffect, useRef } from 'react'
import {
  Search,
  Mic,
  Camera,
  Grid,
  MoreVertical,
} from 'lucide-react'

interface BrowserViewportProps {
  currentUrl: string
  activeTabTitle: string
  layoutVersion: boolean
  searchQuery?: string
  onSearch?: (query: string) => void
}

export const BrowserViewport: React.FC<BrowserViewportProps> = ({
  currentUrl: _currentUrl,
  activeTabTitle: _activeTabTitle,
  layoutVersion,
  searchQuery: externalQuery = '',
  onSearch,
}) => {
  const viewportRef = useRef<HTMLDivElement>(null)
  const isElectron = typeof window !== 'undefined' && Boolean(window.electronAPI)

  const [internalQuery, setInternalQuery] = useState(externalQuery)
  const [hasSearched, setHasSearched] = useState(false)
  const [submittedQuery, setSubmittedQuery] = useState('')

  // Dynamically sync WebContentsView bounds with this React viewport area
  useLayoutEffect(() => {
    if (!isElectron || !viewportRef.current) return

    const updateBounds = () => {
      if (!viewportRef.current || !window.electronAPI) return
      const rect = viewportRef.current.getBoundingClientRect()
      if (rect.width > 0 && rect.height > 0) {
        window.electronAPI.updateViewportBounds({
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
        })
      }
    }

    updateBounds()
    const rafId = requestAnimationFrame(updateBounds)
    const timerId = window.setTimeout(updateBounds, 100)

    const observer = new ResizeObserver(() => {
      updateBounds()
    })
    observer.observe(viewportRef.current)
    // The side panel changes the width of this element's flex parent. Observe
    // both boxes so the native view follows that layout change immediately.
    if (viewportRef.current.parentElement) {
      observer.observe(viewportRef.current.parentElement)
    }
    window.addEventListener('resize', updateBounds)

    return () => {
      cancelAnimationFrame(rafId)
      window.clearTimeout(timerId)
      observer.disconnect()
      window.removeEventListener('resize', updateBounds)
    }
  }, [isElectron, layoutVersion])

  useEffect(() => {
    if (externalQuery) {
      setInternalQuery(externalQuery)
      setSubmittedQuery(externalQuery)
      setHasSearched(true)
    } else {
      setHasSearched(false)
      setSubmittedQuery('')
      setInternalQuery('')
    }
  }, [externalQuery])

  const handleSearchSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    const q = internalQuery.trim()
    if (!q) return
    setSubmittedQuery(q)
    setHasSearched(true)
    onSearch?.(q)
  }

  const handleResetToHome = () => {
    setHasSearched(false)
    setSubmittedQuery('')
    setInternalQuery('')
  }

  // Real Electron WebContentsView mount container
  if (isElectron) {
    return (
      <div
        ref={viewportRef}
        id="real-browser-viewport-mount"
        className="flex-1 h-full w-full bg-white relative overflow-hidden"
      />
    )
  }

  // Fallback for standard browser preview
  return (
    <div className="flex-1 h-full overflow-y-auto bg-white text-gray-800 flex flex-col select-text font-sans antialiased">
      {/* Search Results View */}
      {hasSearched ? (
        <div className="flex flex-col min-h-full">
          {/* Results Top Header */}
          <header className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-3 flex flex-wrap items-center justify-between gap-4 shadow-xs">
            <div className="flex items-center gap-6 flex-1 max-w-3xl">
              {/* Google Wordmark Logo */}
              <button
                type="button"
                onClick={handleResetToHome}
                className="text-xl font-medium tracking-tight select-none cursor-pointer flex items-center font-serif"
                title="Back to Google Home"
              >
                <span className="text-[#4285F4] font-bold">G</span>
                <span className="text-[#EA4335] font-bold">o</span>
                <span className="text-[#FBBC05] font-bold">o</span>
                <span className="text-[#4285F4] font-bold">g</span>
                <span className="text-[#34A853] font-bold">l</span>
                <span className="text-[#EA4335] font-bold">e</span>
              </button>

              {/* Search Bar in Header */}
              <form onSubmit={handleSearchSubmit} className="flex-1 max-w-2xl">
                <div className="flex items-center bg-white border border-gray-200 hover:shadow-md focus-within:shadow-md rounded-full px-4 py-2 gap-3 transition-shadow">
                  <input
                    type="text"
                    value={internalQuery}
                    onChange={(e) => setInternalQuery(e.target.value)}
                    placeholder="Search Google or type a URL"
                    className="flex-1 bg-transparent text-sm text-gray-900 focus:outline-none"
                  />
                  <div className="flex items-center gap-2 text-gray-500">
                    <Mic className="w-4 h-4 text-[#4285F4] cursor-pointer hover:opacity-80" />
                    <Camera className="w-4 h-4 text-[#EA4335] cursor-pointer hover:opacity-80" />
                    <Search
                      onClick={() => handleSearchSubmit()}
                      className="w-4 h-4 text-[#4285F4] cursor-pointer hover:opacity-80 ml-1"
                    />
                  </div>
                </div>
              </form>
            </div>

            {/* Profile / Controls */}
            <div className="flex items-center gap-3">
              <button type="button" className="p-2 text-gray-600 hover:bg-gray-100 rounded-full" title="Google apps">
                <Grid className="w-4 h-4" />
              </button>
              <div className="w-8 h-8 rounded-full bg-[#1a73e8] text-white flex items-center justify-center text-xs font-semibold shadow-xs">
                S
              </div>
            </div>
          </header>

          {/* Search Filter Tabs */}
          <div className="border-b border-gray-200 px-6 sm:px-36 text-xs text-gray-600 flex items-center gap-6 py-2">
            <span className="text-[#1a73e8] border-b-2 border-[#1a73e8] pb-1.5 font-medium cursor-pointer">
              All
            </span>
            <span className="hover:text-gray-900 cursor-pointer pb-1.5">Images</span>
            <span className="hover:text-gray-900 cursor-pointer pb-1.5">Videos</span>
            <span className="hover:text-gray-900 cursor-pointer pb-1.5">News</span>
            <span className="hover:text-gray-900 cursor-pointer pb-1.5">Tools</span>
          </div>

          {/* Results Content */}
          <main className="flex-1 px-6 sm:px-36 py-4 max-w-4xl">
            <div className="text-xs text-gray-500 mb-5">
              About 1,420,000 results (0.28 seconds) for <strong className="text-gray-700">"{submittedQuery}"</strong>
            </div>

            {/* Simulated Realistic Search Result 1 */}
            <div className="mb-7 group">
              <div className="flex items-center gap-2 text-xs text-gray-600 mb-1">
                <span className="w-4 h-4 rounded-full bg-gray-100 flex items-center justify-center text-[10px]">🌐</span>
                <span className="truncate">https://github.com › browser-use › browser-use</span>
                <MoreVertical className="w-3 h-3 text-gray-400 opacity-0 group-hover:opacity-100" />
              </div>
              <h3 className="text-lg text-[#1a0dab] hover:underline cursor-pointer font-medium leading-snug">
                browser-use: Open-Source Web Automation Agent with LLMs
              </h3>
              <p className="text-xs text-gray-700 mt-1.5 leading-relaxed">
                Connect your AI agents with the browser. <strong>browser-use</strong> makes websites accessible to LLMs by parsing DOM elements, clicking, typing, and extracting structured information autonomously.
              </p>
              <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
                <span>⭐ 34.8k stars</span>
                <span>•</span>
                <span>Python & Playwright backend</span>
                <span>•</span>
                <span>Supports Claude 3.7 & GPT-4o</span>
              </div>
            </div>

            {/* Simulated Realistic Search Result 2 */}
            <div className="mb-7 group">
              <div className="flex items-center gap-2 text-xs text-gray-600 mb-1">
                <span className="w-4 h-4 rounded-full bg-gray-100 flex items-center justify-center text-[10px]">📄</span>
                <span className="truncate">https://arxiv.org › abs › 2402.04561</span>
              </div>
              <h3 className="text-lg text-[#1a0dab] hover:underline cursor-pointer font-medium leading-snug">
                A Survey on Autonomous Web Navigation & GUI Grounding Agents
              </h3>
              <p className="text-xs text-gray-700 mt-1.5 leading-relaxed">
                Comprehensive evaluation of multimodal LLM agents operating real web browsers. Benchmarks on Mind2Web, WebArena, and complex multi-page task completion.
              </p>
            </div>

            {/* Simulated Realistic Search Result 3 */}
            <div className="mb-7 group">
              <div className="flex items-center gap-2 text-xs text-gray-600 mb-1">
                <span className="w-4 h-4 rounded-full bg-gray-100 flex items-center justify-center text-[10px]">⚡</span>
                <span className="truncate">https://anthropic.com › research › computer-use</span>
              </div>
              <h3 className="text-lg text-[#1a0dab] hover:underline cursor-pointer font-medium leading-snug">
                Anthropic Computer Use & Browser Agent Evaluation
              </h3>
              <p className="text-xs text-gray-700 mt-1.5 leading-relaxed">
                Developing AI that can use a computer the way people do — by looking at a screen, moving a cursor, clicking buttons, and typing text.
              </p>
            </div>
          </main>

          {/* Minimal Search Footer */}
          <footer className="bg-[#f2f2f2] text-xs text-gray-600 border-t border-gray-200 px-6 sm:px-36 py-3 flex items-center justify-between">
            <span>India • From your IP address</span>
            <div className="flex items-center gap-4">
              <span className="hover:underline cursor-pointer">Help</span>
              <span className="hover:underline cursor-pointer">Send feedback</span>
              <span className="hover:underline cursor-pointer">Privacy</span>
              <span className="hover:underline cursor-pointer">Terms</span>
            </div>
          </footer>
        </div>
      ) : (
        /* Pristine Google Homepage View */
        <div className="flex flex-col justify-between min-h-full">
          {/* Top Navigation Links */}
          <header className="flex items-center justify-end px-6 py-3.5 gap-4 text-xs text-gray-700 font-normal select-none">
            <a href="#gmail" onClick={(e) => e.preventDefault()} className="hover:underline">
              Gmail
            </a>
            <a href="#images" onClick={(e) => e.preventDefault()} className="hover:underline">
              Images
            </a>
            <button
              type="button"
              className="p-2 text-gray-600 hover:bg-gray-100 rounded-full transition-colors"
              title="Google apps"
            >
              <Grid className="w-4 h-4" />
            </button>
            <button
              type="button"
              className="bg-[#1a73e8] hover:bg-[#1557b0] text-white px-5 py-2 rounded font-medium text-xs shadow-xs transition-colors"
            >
              Sign in
            </button>
          </header>

          {/* Main Centered Google Search Hero */}
          <main className="flex flex-col items-center justify-center px-4 py-8 flex-1">
            {/* Google Colorful Wordmark Logo */}
            <div className="mb-7 select-none">
              <h1 className="text-7xl sm:text-8xl font-normal tracking-tight font-sans">
                <span className="text-[#4285F4] font-semibold">G</span>
                <span className="text-[#EA4335] font-semibold">o</span>
                <span className="text-[#FBBC05] font-semibold">o</span>
                <span className="text-[#4285F4] font-semibold">g</span>
                <span className="text-[#34A853] font-semibold">l</span>
                <span className="text-[#EA4335] font-semibold">e</span>
              </h1>
            </div>

            {/* Large Centered Search Box */}
            <form onSubmit={handleSearchSubmit} className="w-full max-w-[584px] mb-7">
              <div className="flex items-center w-full bg-white border border-[#dfe1e5] hover:border-transparent hover:shadow-[0_1px_6px_rgba(32,33,36,0.28)] focus-within:border-transparent focus-within:shadow-[0_1px_6px_rgba(32,33,36,0.28)] rounded-full px-4 py-3 gap-3 transition-all">
                <Search className="w-4 h-4 text-[#9aa0a6] shrink-0" />
                <input
                  type="text"
                  value={internalQuery}
                  onChange={(e) => setInternalQuery(e.target.value)}
                  placeholder=""
                  className="w-full bg-transparent text-sm text-gray-900 focus:outline-none"
                  autoFocus
                />
                <div className="flex items-center gap-2.5 text-[#4285F4] shrink-0">
                  <button
                    type="button"
                    onClick={() => {
                      setInternalQuery('open-source AI browser agents')
                    }}
                    title="Search by voice"
                    className="hover:opacity-80"
                  >
                    <Mic className="w-4 h-4 text-[#4285F4]" />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleSearchSubmit()}
                    title="Search by image"
                    className="hover:opacity-80"
                  >
                    <Camera className="w-4 h-4 text-[#EA4335]" />
                  </button>
                </div>
              </div>

              {/* Action Buttons: "Google Search" and "I'm Feeling Lucky" */}
              <div className="flex items-center justify-center gap-3 mt-6">
                <button
                  type="submit"
                  className="bg-[#f8f9fa] hover:bg-[#f1f3f4] text-[#3c4043] border border-[#f8f9fa] hover:border-[#dadce0] hover:shadow-xs px-4 py-2 rounded text-xs font-normal transition-all"
                >
                  Google Search
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setInternalQuery('autonomous browser navigation')
                    setSubmittedQuery('autonomous browser navigation')
                    setHasSearched(true)
                  }}
                  className="bg-[#f8f9fa] hover:bg-[#f1f3f4] text-[#3c4043] border border-[#f8f9fa] hover:border-[#dadce0] hover:shadow-xs px-4 py-2 rounded text-xs font-normal transition-all"
                >
                  I'm Feeling Lucky
                </button>
              </div>
            </form>

            {/* Languages offered */}
            <div className="text-xs text-[#4d5156] text-center flex flex-wrap items-center justify-center gap-1.5 select-none">
              <span>Google offered in:</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">हिन्दी</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">বাংলা</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">తెలుగు</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">मराठी</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">தமிழ்</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">ગુજરાતી</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">ಕನ್ನಡ</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">മലയാളം</span>
              <span className="text-[#1a0dab] hover:underline cursor-pointer">ਪੰਜਾਬੀ</span>
            </div>
          </main>

          {/* Authentic Google Footer */}
          <footer className="bg-[#f2f2f2] text-xs text-[#70757a] select-none">
            <div className="px-6 py-3 border-b border-[#dadce0]">
              <span>India</span>
            </div>
            <div className="px-6 py-3 flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-6">
                <span className="hover:underline cursor-pointer">About</span>
                <span className="hover:underline cursor-pointer">Advertising</span>
                <span className="hover:underline cursor-pointer">Business</span>
                <span className="hover:underline cursor-pointer">How Search works</span>
              </div>
              <div className="flex items-center gap-6">
                <span className="hover:underline cursor-pointer">Privacy</span>
                <span className="hover:underline cursor-pointer">Terms</span>
                <span className="hover:underline cursor-pointer">Settings</span>
              </div>
            </div>
          </footer>
        </div>
      )}
    </div>
  )
}
