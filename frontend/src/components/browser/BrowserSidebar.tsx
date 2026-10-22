import React, { useState } from 'react'
import {
  Layers,
  Bookmark,
  History,
  Download,
  Puzzle,
  Code2,
  Settings,
  HelpCircle,
  type LucideIcon,
} from 'lucide-react'

interface SidebarItem {
  id: string
  label: string
  icon: LucideIcon
  shortcut?: string
}

const SIDEBAR_TOP_ITEMS: SidebarItem[] = [
  { id: 'tabs', label: 'Tabs & Spaces', icon: Layers, shortcut: '⌘1' },
  { id: 'bookmarks', label: 'Bookmarks', icon: Bookmark, shortcut: '⌘B' },
  { id: 'history', label: 'Browsing History', icon: History, shortcut: '⌘Y' },
  { id: 'downloads', label: 'Downloads', icon: Download, shortcut: '⌘J' },
  { id: 'extensions', label: 'Browser Extensions', icon: Puzzle },
  { id: 'devtools', label: 'Agent DOM Inspector', icon: Code2, shortcut: '⌥⌘I' },
]

const SIDEBAR_BOTTOM_ITEMS: SidebarItem[] = [
  { id: 'settings', label: 'Browser Settings', icon: Settings },
  { id: 'help', label: 'Copilot Help & Docs', icon: HelpCircle },
]

export const BrowserSidebar: React.FC = () => {
  const [activeItemId, setActiveItemId] = useState<string>('tabs')

  return (
    <aside className="w-12 bg-neutral-950 border-r border-neutral-800/80 flex flex-col items-center py-2.5 justify-between shrink-0 select-none z-10">
      {/* Top Browser Navigation Icons */}
      <div className="flex flex-col items-center gap-1 w-full px-1.5">
        {SIDEBAR_TOP_ITEMS.map((item) => {
          const Icon = item.icon
          const isActive = activeItemId === item.id
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setActiveItemId(item.id)}
              className={`relative group flex items-center justify-center w-8 h-8 rounded-lg transition-all ${
                isActive
                  ? 'bg-neutral-800 text-indigo-400 shadow-xs'
                  : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900'
              }`}
              aria-label={item.label}
            >
              <Icon className="w-4 h-4" />

              {/* Active Indicator Bar */}
              {isActive && (
                <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 bg-indigo-500 rounded-r-full" />
              )}

              {/* Floating Tooltip */}
              <div className="absolute left-full ml-2.5 px-2.5 py-1 bg-neutral-900 border border-neutral-700 text-neutral-200 text-[11px] font-medium rounded-md whitespace-nowrap opacity-0 pointer-events-none group-hover:opacity-100 transition-opacity z-50 shadow-xl flex items-center gap-1.5">
                <span>{item.label}</span>
                {item.shortcut && (
                  <span className="text-[9px] text-neutral-400 font-mono bg-neutral-800 px-1 py-0.5 rounded">
                    {item.shortcut}
                  </span>
                )}
              </div>
            </button>
          )
        })}
      </div>

      {/* Bottom Settings & Help */}
      <div className="flex flex-col items-center gap-1 w-full px-1.5 border-t border-neutral-900 pt-2">
        {SIDEBAR_BOTTOM_ITEMS.map((item) => {
          const Icon = item.icon
          const isActive = activeItemId === item.id
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setActiveItemId(item.id)}
              className={`relative group flex items-center justify-center w-8 h-8 rounded-lg transition-all ${
                isActive
                  ? 'bg-neutral-800 text-indigo-400'
                  : 'text-neutral-500 hover:text-neutral-200 hover:bg-neutral-900'
              }`}
              aria-label={item.label}
            >
              <Icon className="w-4 h-4" />

              {/* Floating Tooltip */}
              <div className="absolute left-full ml-2.5 px-2.5 py-1 bg-neutral-900 border border-neutral-700 text-neutral-200 text-[11px] font-medium rounded-md whitespace-nowrap opacity-0 pointer-events-none group-hover:opacity-100 transition-opacity z-50 shadow-xl">
                <span>{item.label}</span>
              </div>
            </button>
          )
        })}
      </div>
    </aside>
  )
}
