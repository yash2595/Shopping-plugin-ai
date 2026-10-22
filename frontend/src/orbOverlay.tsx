import { StrictMode, useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { SaarthiAvatar } from './components/saarthi/SaarthiAvatar'
import type { AgentRunState } from './types/agentEvents'
import './orbOverlay.css'

const SURFACE_SIZE = 96
const ORB_HIT_RADIUS = 40

export function OrbOverlay() {
  const pointerInsideRef = useRef(false)
  const [runState, setRunState] = useState<AgentRunState>('idle')
  const [overlayMode, setOverlayMode] = useState<'corner' | 'focused'>('corner')
  const [speaking, setSpeaking] = useState(false)

  useEffect(() => window.electronAPI?.onOrbOverlayState((state) => setRunState(state as AgentRunState)), [])
  useEffect(() => window.electronAPI?.onOrbOverlayMode(setOverlayMode), [])
  useEffect(() => window.electronAPI?.onOrbOverlaySpeaking(setSpeaking), [])

  const updatePointerState = useCallback((offsetX: number, offsetY: number) => {
    const surfaceSize = overlayMode === 'focused' ? 280 : SURFACE_SIZE
    const center = surfaceSize / 2
    const isInside = Math.hypot(offsetX - center, offsetY - center) <= (overlayMode === 'focused' ? 118 : ORB_HIT_RADIUS)

    if (pointerInsideRef.current !== isInside) {
      pointerInsideRef.current = isInside
      window.electronAPI?.setOrbPointerInside(isInside)
    }
  }, [overlayMode])

  return (
    <div
      className={`orb-overlay-surface ${overlayMode === 'focused' ? 'orb-overlay-surface--focused' : ''}`}
      onMouseMove={(event) => updatePointerState(event.nativeEvent.offsetX, event.nativeEvent.offsetY)}
      onMouseLeave={() => {
        if (pointerInsideRef.current) {
          pointerInsideRef.current = false
          window.electronAPI?.setOrbPointerInside(false)
        }
      }}
    >
      <SaarthiAvatar
        runState={runState}
        isPanelOpen={false}
        onActivate={() => window.electronAPI?.activateOrb()}
        presentation="native-overlay"
        focused={overlayMode === 'focused'}
        speaking={speaking}
      />
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <OrbOverlay />
  </StrictMode>,
)
