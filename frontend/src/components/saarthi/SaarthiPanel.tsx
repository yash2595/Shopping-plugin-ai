import React, { useEffect, useMemo, useRef } from 'react'
import {
  Activity,
  CheckCircle2,
  Compass,
  Loader2,
  Mic,
  XCircle,
  AlertTriangle,
} from 'lucide-react'
import { ExecutionTimeline } from './ExecutionTimeline'
import { SaarthiHeader } from './SaarthiHeader'
import type { AgentEvent, AgentRunState } from '../../types/agentEvents'
import type { BackendConnectionStatus } from '../../types/assistant'
import { getCurrentAction, getExecutionSteps } from '../../utils/agentExecution'

interface SaarthiPanelProps {
  events: AgentEvent[]
  runState: AgentRunState
  backendStatus?: BackendConnectionStatus
  onSimulateFailure?: () => void
  onClearExecution: () => void
  onTogglePanel: () => void
  onClosePanel?: () => void
  activeTabTitle: string
  userRequest?: string
  parsedIntent?: string
}

export const SaarthiPanel: React.FC<SaarthiPanelProps> = ({
  events,
  runState,
  backendStatus = 'pending',
  onSimulateFailure,
  onClearExecution,
  onTogglePanel,
  onClosePanel,
  activeTabTitle,
  userRequest,
  parsedIntent,
}) => {
  const timelineEndRef = useRef<HTMLDivElement>(null)
  const steps = useMemo(() => getExecutionSteps(events), [events])
  const currentAction = useMemo(() => getCurrentAction(events), [events])

  const isExecuting = runState === 'executing'
  const isCompleted = runState === 'completed'
  const isFailed = runState === 'failed'

  const handleClose = onClosePanel || onTogglePanel

  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [events])

  return (
    <aside className="w-[370px] lg:w-[400px] h-full bg-neutral-950 border-l border-neutral-800 flex flex-col shrink-0 select-none z-10 font-sans shadow-2xl transition-all duration-300">
      {/* 1. Header with dynamic status & close controls */}
      <SaarthiHeader
        runState={runState}
        onClearChat={onClearExecution}
        onTogglePanel={handleClose}
        activeTabTitle={activeTabTitle}
      />

      {/* 2. Scrollable Observable Execution Workspace */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Request & Understood Intent Section */}
        <section className="rounded-xl border border-neutral-800/90 bg-neutral-900/70 p-3.5 space-y-3">
          <div>
            <div className="flex items-center gap-1.5 text-[9px] font-mono uppercase tracking-[0.14em] text-neutral-400">
              <Mic className="h-3 w-3 text-cyan-400" />
              Voice Request
            </div>
            <p className={`mt-1 text-xs leading-relaxed ${userRequest ? 'font-semibold text-white' : 'font-normal text-neutral-400 italic'}`}>
              {userRequest ? `"${userRequest}"` : 'No active voice request — microphone/STT pending integration'}
            </p>
          </div>

          <div className="pt-2.5 border-t border-neutral-800/80">
            <div className="flex items-center gap-1.5 text-[9px] font-mono uppercase tracking-[0.14em] text-cyan-400">
              <Compass className="h-3 w-3 text-cyan-400" />
              Understood Intent
            </div>
            <p className="mt-1 text-[11px] text-neutral-400 leading-relaxed font-mono">
              {parsedIntent || (userRequest ? 'Analyzing request...' : 'Awaiting live AI engine connection')}
            </p>
          </div>
        </section>

        {/* Current Observable Action Card */}
        <section className="rounded-xl border border-neutral-800/90 bg-neutral-900/70 p-3.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-[9px] font-mono uppercase tracking-[0.14em] text-neutral-400">
              <Activity className="h-3 w-3 text-cyan-400" />
              Current Action
            </div>
            <span
              className={`rounded-full border px-2 py-0.5 text-[9px] font-mono uppercase tracking-wider ${
                isCompleted
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                  : isFailed
                    ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                    : isExecuting
                      ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
                      : 'border-neutral-700 bg-neutral-800 text-neutral-400'
              }`}
            >
              {runState}
            </span>
          </div>

          <div
            className={`mt-2.5 rounded-lg border p-3 transition-colors ${
              currentAction
                ? 'border-cyan-500/40 bg-cyan-950/25 text-white'
                : isCompleted
                  ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-200'
                  : isFailed
                    ? 'border-rose-500/40 bg-rose-950/20 text-rose-200'
                    : 'border-neutral-800 bg-neutral-950/50 text-neutral-400'
            }`}
          >
            <div className="flex items-center gap-2">
              {currentAction ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-400 shrink-0" />
              ) : isCompleted ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
              ) : isFailed ? (
                <XCircle className="h-3.5 w-3.5 text-rose-400 shrink-0" />
              ) : (
                <Activity className="h-3.5 w-3.5 text-neutral-500 shrink-0" />
              )}
              <span className="text-xs font-medium">
                {currentAction
                  ? currentAction.message
                  : isCompleted
                    ? 'Execution completed'
                    : isFailed
                      ? 'Action could not be completed'
                      : isExecuting
                        ? 'Executing browser action'
                        : 'Awaiting agent task'}
              </span>
            </div>
          </div>
        </section>

        {/* Observable Timeline */}
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-[10px] font-mono uppercase tracking-[0.14em] text-neutral-400">
              Observable Execution Timeline
            </h2>
            {steps.length > 0 && (
              <span className="text-[10px] font-mono text-neutral-500">
                {steps.length} {steps.length === 1 ? 'action' : 'actions'}
              </span>
            )}
          </div>
          <ExecutionTimeline steps={steps} />
          <div ref={timelineEndRef} />
        </section>

        {/* Lightweight completion acknowledgement; the browser remains the result surface. */}
        {isCompleted && (
          <section className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2.5 animate-in fade-in duration-300">
            <div className="flex items-center gap-2 text-xs font-medium text-emerald-200">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              Execution completed; the browser remains the result surface.
            </div>
          </section>
        )}

        {/* Lightweight failure acknowledgement. */}
        {isFailed && (
          <section className="rounded-lg border border-rose-500/30 bg-rose-950/20 px-3 py-2.5 animate-in fade-in duration-300">
            <div className="flex items-center gap-2 text-xs font-medium text-rose-200">
              <AlertTriangle className="h-4 w-4 text-rose-400 shrink-0" />
              Execution stopped; inspect the browser state for details.
            </div>
          </section>
        )}
      </div>

      {/* 3. Concise Status & Integration Controls Footer */}
      <footer className="border-t border-neutral-800 bg-neutral-950 p-3 shrink-0 select-none">
        <div className="flex items-center justify-between text-[10px] text-neutral-400 font-mono">
          <div className="flex items-center gap-1.5">
            <span
              className={`h-2 w-2 rounded-full ${
                isExecuting
                  ? 'bg-cyan-400 animate-ping'
                  : isCompleted
                    ? 'bg-emerald-400'
                    : isFailed
                      ? 'bg-rose-400'
                      : 'bg-neutral-500'
              }`}
            />
            <span className="text-neutral-400 uppercase tracking-wider">
              {isExecuting ? 'Observing Browser' : isCompleted ? 'Completed' : isFailed ? 'Failed' : 'Ready'}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[10px] text-neutral-500 font-mono">
              Backend: {backendStatus === 'connected' ? 'Connected' : 'Pending'}
            </span>
            {onSimulateFailure && !isExecuting && (
              <>
                <span className="text-neutral-700">•</span>
                <button
                  type="button"
                  onClick={onSimulateFailure}
                  className="text-neutral-500 hover:text-rose-400 transition-colors"
                  title="Simulate failure state for testing"
                >
                  Test Failure
                </button>
              </>
            )}
          </div>
        </div>
      </footer>
    </aside>
  )
}
