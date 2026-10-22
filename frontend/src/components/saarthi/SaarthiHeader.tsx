import React from 'react'
import {
  Compass,
  RotateCcw,
  Minimize2,
  Activity,
  CheckCircle2,
  XCircle,
  Loader2,
} from 'lucide-react'
import type { AgentRunState } from '../../types/agentEvents'

interface SaarthiHeaderProps {
  runState: AgentRunState
  onClearChat: () => void
  onTogglePanel: () => void
  activeTabTitle: string
}

export const SaarthiHeader: React.FC<SaarthiHeaderProps> = ({
  runState,
  onClearChat,
  onTogglePanel,
  activeTabTitle,
}) => {
  const isExecuting = runState === 'executing'
  const isCompleted = runState === 'completed'
  const isFailed = runState === 'failed'

  return (
    <header className="p-3 bg-neutral-950/95 border-b border-neutral-800 flex items-center justify-between shrink-0 select-none">
      {/* Brand & Status Indicator */}
      <div className="flex items-center gap-2.5">
        <div
          className="relative flex items-center justify-center w-7 h-7 rounded-lg text-white shadow-md transition-all"
          style={{
            background: 'radial-gradient(circle at 35% 30%, #38bdf8 0%, #1e40af 50%, #0b193d 90%)',
            boxShadow: '0 0 15px rgba(56, 189, 248, 0.4)',
          }}
        >
          <Compass className="w-3.5 h-3.5 text-cyan-200" />
          <span className="absolute -top-0.5 -right-0.5 flex h-2 w-2">
            {isExecuting && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
            )}
            <span
              className={`relative inline-flex rounded-full h-2 w-2 ${
                isCompleted
                  ? 'bg-emerald-400'
                  : isFailed
                    ? 'bg-rose-500'
                    : isExecuting
                      ? 'bg-cyan-400'
                      : 'bg-emerald-500'
              }`}
            />
          </span>
        </div>

        <div>
          <div className="flex items-center gap-1.5">
            <span className="font-bold tracking-tight text-white text-xs">
              COPILOT
            </span>
            <span
              className={`text-[9px] font-mono font-medium px-1.5 py-0.2 rounded border ${
                isCompleted
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                  : isFailed
                    ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                    : isExecuting
                      ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
                      : 'border-neutral-700 bg-neutral-800 text-neutral-400'
              }`}
            >
              {runState.toUpperCase()}
            </span>
          </div>
          <div className="flex items-center gap-1 text-[10px] text-neutral-400 mt-0.5">
            {isExecuting ? (
              <Loader2 className="w-2.5 h-2.5 animate-spin text-cyan-400" />
            ) : isCompleted ? (
              <CheckCircle2 className="w-2.5 h-2.5 text-emerald-400" />
            ) : isFailed ? (
              <XCircle className="w-2.5 h-2.5 text-rose-400" />
            ) : (
              <Activity className="w-2.5 h-2.5 text-emerald-400" />
            )}
            <span className="truncate max-w-[170px] text-neutral-300 font-sans">{activeTabTitle}</span>
          </div>
        </div>
      </div>

      {/* Action Controls */}
      <div className="flex items-center gap-1 text-neutral-400">
        <button
          type="button"
          onClick={onClearChat}
          className="p-1.5 rounded-lg hover:text-white hover:bg-neutral-800/80 transition-colors"
          title="Reset execution timeline"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          onClick={onTogglePanel}
          className="p-1.5 rounded-lg hover:text-white hover:bg-neutral-800/80 transition-colors"
          title="Close Copilot panel"
        >
          <Minimize2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </header>
  )
}
