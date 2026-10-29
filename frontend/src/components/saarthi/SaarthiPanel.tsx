import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import type { BackendConnectionStatus, ShoppingPanelIntegration } from '../../types/assistant'
import { getCurrentAction, getExecutionSteps } from '../../utils/agentExecution'

interface SaarthiPanelProps extends ShoppingPanelIntegration {
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
  navigationRun, onPauseNavigation, onResumeNavigation,
  analysisConsent = false, onAnalysisConsent, onEraseStoredData, onCheckUsage, privacyStatus, diagnosticReport,
  conversation = [], onVoiceListening, analysis, analysisError, onAskShopping, onStopShopping,
}) => {
  const [draft, setDraft] = useState('')
  const [voiceStatus, setVoiceStatus] = useState('')
  const [speechConsent, setSpeechConsent] = useState(false)
  const [confirmErase, setConfirmErase] = useState(false)
  const [erasing, setErasing] = useState(false)
  const [showDiagnostic, setShowDiagnostic] = useState(false)
  const recognition = useRef<{ abort: () => void } | null>(null)
  const stopRecognition = useCallback(() => {
    const current = recognition.current
    recognition.current = null
    current?.abort()
    if (current) onVoiceListening?.(false)
  }, [onVoiceListening])
  const timelineEndRef = useRef<HTMLDivElement>(null)
  const steps = useMemo(() => getExecutionSteps(events), [events])
  const currentAction = useMemo(() => getCurrentAction(events), [events])

  const isExecuting = runState === 'executing'
  const isCompleted = runState === 'completed'
  const isFailed = runState === 'failed'
  const busy = runState === 'processing' || isExecuting

  useEffect(() => () => {
    stopRecognition()
    window.speechSynthesis?.cancel()
    window.electronAPI?.setOrbSpeaking(false)
  }, [stopRecognition])

  const startVoice = () => {
    if (!speechConsent) { setVoiceStatus('Allow browser speech in Data and privacy before using the microphone.'); return }
    type Recognition = { start: () => void; abort: () => void; lang: string; interimResults: boolean;
      onresult: ((event: { results: { [key: number]: { [key: number]: { transcript: string } } } }) => void) | null;
      onerror: (() => void) | null; onend: (() => void) | null }
    const host = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition }
    const Engine = host.SpeechRecognition || host.webkitSpeechRecognition
    if (!Engine) { setVoiceStatus('Voice is unavailable here. You can type your question.'); return }
    window.speechSynthesis?.cancel()
    stopRecognition()
    const recorder = new Engine()
    recognition.current = recorder
    recorder.lang = 'hi-IN'
    recorder.interimResults = false
    recorder.onresult = (event) => { if (recognition.current !== recorder) return; setDraft(event.results[0][0].transcript.slice(0, 2000)); setVoiceStatus('Check or edit the transcript, then ask.') }
    recorder.onerror = () => { if (recognition.current === recorder) { onVoiceListening?.(false); setVoiceStatus('Microphone access or recognition failed. Type your question instead.') } }
    recorder.onend = () => { if (recognition.current === recorder) { recognition.current = null; onVoiceListening?.(false); setVoiceStatus((current) => current.startsWith('Listening') ? 'No transcript received. You can type your question.' : current) } }
    try { recorder.start(); onVoiceListening?.(true); setVoiceStatus('Listening. Browser speech may use online processing.') }
    catch { recognition.current = null; onVoiceListening?.(false); setVoiceStatus('Voice could not start. Type your question instead.') }
  }

  const speakAnswer = () => {
    if ((!analysis && !conversation.length) || !window.speechSynthesis) return
    const voice = window.speechSynthesis.getVoices().find((item) => item.localService && item.lang.startsWith('hi'))
      || window.speechSynthesis.getVoices().find((item) => item.localService)
    if (!voice) { setVoiceStatus('A local voice is unavailable. The text answer remains here.'); return }
    const spoken = (analysis?.answer || [...conversation].reverse().find((turn) => turn.role === 'assistant')?.text || '') + '\n' + (analysis?.evidence || []).slice(0, 5).map((item) =>
      item.kind === 'review' ? `Captured review excerpt: ${item.value}` : `${item.label}: ${item.value}`).join('\n')
    const speech = new SpeechSynthesisUtterance(Array.from(spoken).slice(0, 2000).join(''))
    speech.voice = voice
    speech.onstart = () => window.electronAPI?.setOrbSpeaking(true)
    speech.onend = () => window.electronAPI?.setOrbSpeaking(false)
    speech.onerror = (event) => { window.electronAPI?.setOrbSpeaking(false); reportSpeechError(event) }
    const reportSpeechError = (event: SpeechSynthesisErrorEvent) => { if (!['canceled', 'interrupted'].includes(event.error)) setVoiceStatus('Read aloud failed. The text answer remains available.') }
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(speech)
  }

  const handleClose = onClosePanel || onTogglePanel

  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [events])

  return (
    <aside className="w-[370px] lg:w-[400px] h-full bg-neutral-950 border-l border-neutral-800 flex flex-col shrink-0 select-none z-10 font-sans shadow-2xl transition-all duration-300">
      {/* 1. Header with dynamic status & close controls */}
      <SaarthiHeader
        runState={runState}
        onClearChat={() => { setDraft(''); setVoiceStatus(''); stopRecognition(); onClearExecution() }}
        onTogglePanel={handleClose}
        activeTabTitle={activeTabTitle}
      />

      {/* 2. Scrollable Observable Execution Workspace */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <details className="text-xs text-neutral-300 space-y-3" open={!analysisConsent}>
          <summary className="cursor-pointer font-semibold">Data and privacy · Desktop preview</summary>
          <p className="mt-2">When you ask, your words and up to four selected product captures—URLs, specs and review excerpts—may go to the configured backend and its local or cloud model. The backend creates an anonymous identity or uses configured sign-in. Shopping conversations and preferences can be stored. Retailer cookies, password fields and payment fields are not read.</p>
          <label className="flex gap-2 mt-2"><input type="checkbox" aria-label="Allow shopping analysis" checked={analysisConsent} disabled={erasing}
            onChange={(event) => { stopRecognition(); setConfirmErase(false); onAnalysisConsent?.(event.target.checked) }} />Allow backend and configured model processing for shopping analysis</label>
          <label className="flex gap-2 mt-2"><input type="checkbox" aria-label="Allow browser speech" checked={speechConsent} disabled={erasing}
            onChange={(event) => { if (!event.target.checked) stopRecognition(); setSpeechConsent(event.target.checked) }} />Allow browser speech recognition. Audio may be processed online by the browser's speech service.</label>
          <p className="mt-2">You can withdraw either permission here. Clear removes local conversation and captures; it does not erase stored server records. Closing the app resets these permissions. Navigation step IDs and results stay in memory until app exit to prevent duplicate actions.</p>
          <div className="flex gap-2 flex-wrap mt-2">
            <button type="button" className="underline" disabled={erasing || busy} onClick={() => void onCheckUsage?.()}>Check AI usage</button>
            <button type="button" className="underline" disabled={erasing || busy || !onEraseStoredData} onClick={() => setConfirmErase((value) => !value)}>Delete stored shopping data</button>
          </div>
          {confirmErase && <div className="space-y-2 mt-2">
            <p>This removes your shopping sessions, checkpoints, captures, price observations, alerts and owned review summaries from the active database. Identity/authentication and daily usage counters remain. Backups and external model records have separate retention. Other active devices can create new records.</p>
            <button type="button" className="text-red-300 underline" disabled={erasing || busy} onClick={async () => {
              stopRecognition(); setSpeechConsent(false); setErasing(true)
              try { await onEraseStoredData?.() } finally { setErasing(false); setConfirmErase(false) }
            }}>{erasing ? 'Deleting…' : 'Confirm deletion'}</button>
            <button type="button" className="ml-3 underline" disabled={erasing} onClick={() => setConfirmErase(false)}>Cancel</button>
          </div>}
          {privacyStatus && <p role="status" className="mt-2 whitespace-pre-wrap select-text">{privacyStatus}</p>}
          <p className="mt-2 text-neutral-500">Free desktop preview. No paid subscriptions or checkout are enabled.</p>
        </details>
        <form className="space-y-2" onSubmit={(event) => {
          event.preventDefault()
          stopRecognition()
          if (draft.trim() && !busy) void onAskShopping?.(draft.trim())
        }}>
          <label htmlFor="shopping-question" className="text-xs text-neutral-300">Talk to Saarthi</label>
          <textarea id="shopping-question" value={draft} onChange={(event) => setDraft(event.target.value)}
            maxLength={2000} rows={3} placeholder="Amazon kholo · Search headphones on Flipkart · Battery kitni hai?"
            className="w-full rounded-lg bg-neutral-900 border border-neutral-700 p-3 text-sm text-white select-text" />
          <div className="flex gap-2">
            <button type="submit" disabled={busy || !draft.trim() || !onAskShopping} className="rounded bg-cyan-700 px-3 py-2 text-xs disabled:opacity-40">{busy ? 'Checking…' : 'Ask'}</button>
            <button type="button" onClick={startVoice} disabled={busy || erasing} className="rounded bg-neutral-800 px-3 py-2 text-xs">Mic</button>
            <button type="button" onClick={() => { stopRecognition(); window.speechSynthesis?.cancel(); onStopShopping?.(); setVoiceStatus('Stopped.') }} className="rounded bg-neutral-800 px-3 py-2 text-xs">Stop</button>
            {(analysis || conversation.some((turn) => turn.role === 'assistant')) && <button type="button" onClick={speakAnswer} className="rounded bg-neutral-800 px-3 py-2 text-xs">Read aloud</button>}
          </div>
          <p className="text-[10px] text-neutral-500">Capture products by asking on each page, then ask to compare. Clear removes this session’s captures.</p>
          {voiceStatus && <p role="status" className="text-xs text-neutral-400">{voiceStatus}</p>}
        </form>
        <div className="text-xs text-neutral-400">
          <button type="button" className="underline" onClick={() => setShowDiagnostic((value) => !value)}>Report a page-reading problem</button>
          {showDiagnostic && <div className="space-y-2 mt-2"><p>Copy this local report to a project issue yourself. It excludes addresses, page text, product/account IDs and secrets. Nothing is uploaded automatically.</p><textarea aria-label="Local page diagnostic" readOnly rows={8} value={diagnosticReport || 'No report available.'} className="w-full rounded bg-neutral-900 p-2 select-text" /></div>}
        </div>
        {navigationRun && <section aria-label="Navigation plan" data-status={navigationRun.status} className="space-y-2 text-xs text-neutral-300">
          <p className="font-semibold">Browser plan · {navigationRun.status}</p>
          <ol className="list-decimal pl-4 space-y-1">{navigationRun.steps.map((step) => <li key={step.step_id}>{step.command.kind.replaceAll('_', ' ')} · {step.status}</li>)}</ol>
          <p role="status">{navigationRun.message}</p>
          {navigationRun.status === 'executing' && <button type="button" className="underline" onClick={onPauseNavigation}>Pause after this step</button>}
          {navigationRun.can_resume && <button type="button" className="underline" onClick={() => void onResumeNavigation?.()}>Resume browser plan</button>}
        </section>}
        <section aria-label="Conversation" aria-live="polite" className="space-y-2 select-text">
          {conversation.map((turn) => <article key={turn.id} className={`rounded-lg p-3 text-sm whitespace-pre-wrap ${turn.role === 'user' ? 'bg-cyan-950 ml-5' : 'bg-neutral-900 mr-2'}`}><span className="block text-[10px] text-neutral-400">{turn.role === 'user' ? 'You' : 'Saarthi'}</span>{turn.text}</article>)}
        </section>
        {analysisError && <p role="alert" className="text-xs text-amber-300">{analysisError}</p>}
        {analysis && <section aria-label="Shopping answer" className="space-y-3 select-text">
          <p className="text-sm text-neutral-100">{analysis.answer}</p>
          {analysis.inference_fallback && <p className="text-xs text-amber-300">{analysis.inference_fallback === 'cloud' ? 'The configured cloud fallback was used for this question.' : 'Local inference was unavailable; captured-fact rules were used.'}</p>}
          {analysis.follow_up && <p className="text-xs text-cyan-300">{analysis.follow_up}</p>}
          {analysis.evidence.map((item) => <article key={item.evidence_id} className="rounded border border-neutral-800 bg-neutral-900 p-3">
            <p className="text-[10px] text-cyan-300">{item.product_title} · {item.kind === 'review' ? `${item.rating} stars · captured excerpt` : item.label}</p>
            <p className="mt-1 text-sm whitespace-pre-wrap">{item.kind === 'review' ? `“${item.value}”` : item.value}</p>
            {item.verified_purchase_observed === true && <p className="text-[10px] text-neutral-400">Page showed a verified-purchase badge</p>}
            <a href={item.source_url} target="_blank" rel="noreferrer" className="text-[10px] text-neutral-500 underline">Source · {new Date(item.observed_at).toLocaleTimeString()}</a>
            {(item.kind === 'spec' || item.kind === 'review') && <button type="button" className="ml-2 text-[10px] text-cyan-300 underline" onClick={async () => {
              const result = await window.electronAPI?.showAnalysisEvidence({
                skill: item.kind === 'spec' ? 'highlight_spec' : 'scroll_to_reviews',
                document_id: item.document_id, source_url: item.source_url,
                spec_key: item.kind === 'spec' ? item.label : null, review_id: item.review_id, expected_text: item.value,
              })
              setVoiceStatus(result?.success ? 'Evidence highlighted on the page.' : result?.error || 'Page highlighting is unavailable.')
            }}>Show on page</button>}
          </article>)}
          {analysis.comparison.length > 0 && <div className="overflow-x-auto"><table className="text-xs w-full"><thead><tr><th className="text-left p-1">Detail</th>{Object.keys(analysis.comparison[0].values).map((id, index) => <th key={id} className="p-1">{analysis.evidence.find((item) => item.product_id === id)?.product_title || `Product ${index + 1}`}</th>)}</tr></thead><tbody>{analysis.comparison.map((row) => <tr key={row.label}><td className="p-1">{row.label}</td>{Object.entries(row.values).map(([id, value]) => <td key={id} className="p-1">{value ?? 'Unknown'}</td>)}</tr>)}</tbody></table></div>}
          {analysis.shortlist.length > 0 && <p className="text-xs text-emerald-300">Observed-price shortlist: {analysis.shortlist.map((id, index) => analysis.evidence.find((item) => item.product_id === id)?.product_title || `Product ${index + 1}`).join(' → ')}</p>}
          {analysis.warnings.map((warning) => <p key={warning} className="text-xs text-amber-300">{warning === 'LOCAL_CAPTURE_FALLBACK' ? 'Captured details only. Reconnect for question-specific analysis.' : warning.startsWith('STALE_') ? 'A saved capture is old. Ask again on that product page to refresh it.' : warning === 'ITEM_PRICE_ONLY_FINAL_TOTAL_UNKNOWN' ? 'The shortlist uses item prices. Delivery charges and the final checkout total may differ.' : warning === 'MIXED_CURRENCIES_NO_CONVERSION' ? 'Prices have different currencies. No currency conversion was applied.' : 'Some price, stock or currency details are missing. Check the source page.'}</p>)}
          <p className="text-[10px] text-neutral-500">{analysis.coverage}</p>
        </section>}
        {/* Request & Understood Intent Section */}
        <section className="rounded-xl border border-neutral-800/90 bg-neutral-900/70 p-3.5 space-y-3">
          <div>
            <div className="flex items-center gap-1.5 text-[9px] font-mono uppercase tracking-[0.14em] text-neutral-400">
              <Mic className="h-3 w-3 text-cyan-400" />
              Shopping Question
            </div>
            <p className={`mt-1 text-xs leading-relaxed ${userRequest ? 'font-semibold text-white' : 'font-normal text-neutral-400 italic'}`}>
              {userRequest ? `"${userRequest}"` : 'Type a shopping question or use the microphone.'}
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
                    ? 'Analysis finished'
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
              Analysis finished. Check the captured facts and sources above.
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
              Backend: {backendStatus === 'connected' ? 'Connected' : backendStatus === 'disconnected' ? 'Unavailable' : 'Pending'}
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
