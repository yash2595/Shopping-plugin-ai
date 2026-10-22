import React, { useEffect, useLayoutEffect, useRef } from 'react'
import type { AgentRunState } from '../../types/agentEvents'
import type { OrbOverlayBounds } from '../../types/electron'
import './SaarthiAvatar.css'

interface SaarthiAvatarProps {
  runState: AgentRunState
  isPanelOpen: boolean
  onActivate: () => void
  presentation?: 'renderer' | 'native-overlay'
  focused?: boolean
  speaking?: boolean
  onBoundsChange?: (bounds: OrbOverlayBounds | null) => void
}

const AVATAR_ASSETS: Record<AgentRunState, string> = {
  idle: './avatar/idle.png',
  listening: './avatar/listening.png',
  processing: './avatar/processing.png',
  executing: './avatar/saarthi-avatar.png',
  completed: './avatar/completed.png',
  failed: './avatar/failed.png',
}

export const SaarthiAvatar: React.FC<SaarthiAvatarProps> = ({ runState, isPanelOpen, onActivate, presentation = 'renderer', focused = false, speaking = false, onBoundsChange }) => {
  const anchorRef = useRef<HTMLDivElement>(null)
  const speakingVideoRef = useRef<HTMLVideoElement>(null)
  const visible = !isPanelOpen

  useEffect(() => {
    const video = speakingVideoRef.current
    if (!video) return
    if (speaking) {
      video.currentTime = 0
      void video.play().catch(() => console.warn('[SaarthiAvatar] Speaking animation unavailable'))
    } else {
      video.pause()
      video.currentTime = 0
    }
  }, [speaking])

  useLayoutEffect(() => {
    if (!onBoundsChange) return
    const anchor = anchorRef.current
    if (!anchor || !visible) { onBoundsChange(null); return }
    const report = () => { const rect = anchor.getBoundingClientRect(); onBoundsChange({ x: rect.x, y: rect.y, width: rect.width, height: rect.height }) }
    report()
    const observer = new ResizeObserver(report)
    observer.observe(anchor)
    window.addEventListener('resize', report)
    return () => { observer.disconnect(); window.removeEventListener('resize', report); onBoundsChange(null) }
  }, [onBoundsChange, visible])

  if (!visible) return null
  return (
    <div ref={anchorRef} className={`saarthi-avatar-anchor ${presentation === 'native-overlay' ? 'saarthi-avatar-anchor--overlay' : ''} ${focused ? 'saarthi-avatar-anchor--focused' : ''}`}>
      <button type="button" className={`saarthi-avatar ${runState}`} onClick={onActivate} aria-label="Activate Copilot assistant" title="Activate Copilot">
        <span className={`avatar-art ${speaking ? 'avatar-art--speaking' : ''}`} aria-hidden="true">
          {(Object.keys(AVATAR_ASSETS) as AgentRunState[]).map((state) => (
            <img
              key={state}
              className={`avatar-image ${state === runState ? 'avatar-image--active' : ''}`}
              src={AVATAR_ASSETS[state]}
              alt=""
              draggable={false}
            />
          ))}
          <video ref={speakingVideoRef} className={`avatar-speaking ${speaking ? 'avatar-speaking--active' : ''}`} src="./avatar/saarthi-speaking.webm" loop muted playsInline aria-hidden="true" />
        </span>
        <span className="avatar-state-dot" aria-hidden="true" />
      </button>
    </div>
  )
}
