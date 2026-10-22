import React from 'react'
import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import type { ExecutionStep } from '../../types/agentEvents'

interface ExecutionTimelineProps {
  steps: ExecutionStep[]
}

function StepIcon({ status }: { status: ExecutionStep['status'] }) {
  if (status === 'completed') return <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
  if (status === 'failed') return <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
  if (status === 'running') return <Loader2 className="w-4 h-4 text-cyan-400 animate-spin shrink-0" />
  return <Circle className="w-4 h-4 text-neutral-600 shrink-0" />
}

function formatActionType(action?: string): string {
  if (!action) return 'ACTION'
  return action.replace(/_/g, ' ').toUpperCase()
}

export const ExecutionTimeline: React.FC<ExecutionTimelineProps> = ({ steps }) => {
  if (steps.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-neutral-800 bg-neutral-900/40 p-4 text-center">
        <p className="text-[11px] font-medium text-neutral-300">Execution timeline ready</p>
        <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">
          Observable browser actions (Navigate, Type, Click, Extract) will appear here.
        </p>
      </div>
    )
  }

  return (
    <ol className="space-y-1.5" aria-label="Observable browser execution timeline">
      {steps.map((step) => {
        const isCurrent = step.status === 'running'
        return (
          <li
            key={step.id}
            className={`flex items-center gap-2.5 rounded-lg border px-2.5 py-2 text-[11px] transition-colors ${
              isCurrent
                ? 'border-cyan-500/40 bg-cyan-950/20 text-white shadow-xs'
                : step.status === 'failed'
                  ? 'border-rose-500/25 bg-rose-950/20 text-rose-100'
                  : 'border-transparent bg-neutral-900/60 text-neutral-300'
            }`}
          >
            <StepIcon status={step.status} />

            <div className="min-w-0 flex-1 flex items-center gap-2">
              {step.action && (
                <span className="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-mono font-medium tracking-tight bg-neutral-800 text-neutral-400 border border-neutral-700/60">
                  {formatActionType(step.action)}
                </span>
              )}
              <span className={`truncate ${isCurrent ? 'font-medium text-white' : 'text-neutral-300'}`}>
                {step.message}
              </span>
            </div>

            <span className={`shrink-0 text-[9px] font-mono uppercase tracking-wider ${
              step.status === 'completed'
                ? 'text-emerald-400'
                : step.status === 'running'
                  ? 'text-cyan-400'
                  : step.status === 'failed'
                    ? 'text-rose-400'
                    : 'text-neutral-500'
            }`}>
              {step.status}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
