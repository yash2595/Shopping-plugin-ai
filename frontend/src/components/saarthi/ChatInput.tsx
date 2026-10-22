import React from 'react'
import { CheckCircle2, Loader2, Mic, Radio, TriangleAlert, Waves } from 'lucide-react'
import type { AgentRunState } from '../../types/agentEvents'

interface ChatInputProps {
  state: AgentRunState
  onStartDemo: () => void
  onSimulateFailure?: () => void
}

const STATE_COPY: Record<AgentRunState, { title: string; detail: string }> = {
  idle: { title: 'Voice control ready', detail: 'Tap the microphone to run the local execution demo.' },
  listening: { title: 'Listening...', detail: 'Waiting for a spoken objective.' },
  processing: { title: 'Processing...', detail: 'Preparing a browser execution plan.' },
  executing: { title: 'SAARTHI is executing...', detail: 'Observable browser actions are shown above.' },
  completed: { title: 'Execution completed', detail: 'The agent is ready for the next objective.' },
  failed: { title: 'Execution needs attention', detail: 'A future backend event can describe the failure here.' },
}

export const ChatInput: React.FC<ChatInputProps> = ({ state, onStartDemo, onSimulateFailure }) => {
  const isActive = state === 'listening' || state === 'processing' || state === 'executing'
  const copy = STATE_COPY[state]
  const Icon = state === 'completed' ? CheckCircle2 : state === 'failed' ? TriangleAlert : Mic

  return (
    <section className="border-t border-neutral-800/80 bg-neutral-950/95 p-3 shrink-0 select-none" aria-label="Voice interaction">
      <div className="rounded-xl border border-neutral-800 bg-neutral-900/80 p-3 shadow-inner">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onStartDemo}
            disabled={isActive}
            title="Run voice interaction demo"
            className={`relative flex h-14 w-14 shrink-0 items-center justify-center rounded-full border transition-all ${
              state === 'listening'
                ? 'border-rose-400/80 bg-rose-500/20 text-rose-200 ring-4 ring-rose-500/15 animate-pulse'
                : state === 'executing' || state === 'processing'
                  ? 'border-indigo-400/70 bg-indigo-500/20 text-indigo-100 ring-4 ring-indigo-500/10'
                  : state === 'completed'
                    ? 'border-emerald-500/60 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/20'
                    : state === 'failed'
                      ? 'border-rose-500/60 bg-rose-500/15 text-rose-200 hover:bg-rose-500/20'
                      : 'border-indigo-500/60 bg-indigo-600/20 text-indigo-100 hover:bg-indigo-600/30 hover:border-indigo-400'
            } disabled:cursor-wait`}
          >
            {state === 'listening' && <Waves className="h-6 w-6 animate-pulse" />}
            {state === 'processing' || state === 'executing' ? <Loader2 className="h-6 w-6 animate-spin" /> : state !== 'listening' && <Icon className="h-6 w-6" />}
            {state === 'listening' && <span className="absolute -inset-1 rounded-full border border-rose-400/45" />}
          </button>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <Radio className={`h-3 w-3 ${isActive ? 'animate-pulse text-indigo-400' : 'text-emerald-400'}`} />
              <span className="text-[10px] font-mono uppercase tracking-[0.12em] text-neutral-500">Voice-first control</span>
            </div>
            <p className="mt-1 text-xs font-semibold text-white">{copy.title}</p>
            <p className="mt-0.5 text-[10px] leading-relaxed text-neutral-400">{copy.detail}</p>
          </div>
        </div>

        <div className="mt-3 flex items-center justify-between border-t border-neutral-800/70 pt-2 text-[10px]">
          <span className="font-mono text-neutral-500">FRONTEND DEMO MODE</span>
          {onSimulateFailure ? (
            <button type="button" onClick={onSimulateFailure} className="text-neutral-500 hover:text-rose-300 transition-colors">
              Test failed state
            </button>
          ) : (
            <span className="text-neutral-500">STT connection pending</span>
          )}
        </div>
      </div>
    </section>
  )
}
