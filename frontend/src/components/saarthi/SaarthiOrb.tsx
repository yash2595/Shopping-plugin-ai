import React, { useRef, useEffect, useCallback, useLayoutEffect } from 'react'
import { Sparkles, Loader2, Compass } from 'lucide-react'
import type { AgentRunState } from '../../types/agentEvents'
import type { OrbOverlayBounds } from '../../types/electron'

/* ═══════════════════════════════════════════════════════════════════════════
   Particle data structure
   ═══════════════════════════════════════════════════════════════════════════ */
interface Particle {
  /** Original pixel position inside the canvas */
  homeX: number
  homeY: number
  /** Current animated position */
  x: number
  y: number
  /** Velocity for physics-based motion */
  vx: number
  vy: number
  /** RGBA colour sampled from the reference image */
  r: number
  g: number
  b: number
  a: number
  /** Per-particle random phase for wave variety */
  wavePhase: number
}

/* ═══════════════════════════════════════════════════════════════════════════
   Animation parameters per agent state
   ═══════════════════════════════════════════════════════════════════════════ */
interface AnimParams {
  breatheSpeed: number
  breatheAmount: number
  waveAmp: number
  waveSpeed: number
  waveFreq: number
}

const STATE_PARAMS: Record<string, AnimParams> = {
  idle:       { breatheSpeed: 0.0008, breatheAmount: 0.012, waveAmp: 2.0,  waveSpeed: 0.0010, waveFreq: 0.07 },
  listening:  { breatheSpeed: 0.0014, breatheAmount: 0.020, waveAmp: 3.5,  waveSpeed: 0.0020, waveFreq: 0.09 },
  processing: { breatheSpeed: 0.0018, breatheAmount: 0.016, waveAmp: 2.8,  waveSpeed: 0.0025, waveFreq: 0.11 },
  executing:  { breatheSpeed: 0.0008, breatheAmount: 0.010, waveAmp: 1.8,  waveSpeed: 0.0010, waveFreq: 0.07 },
  completed:  { breatheSpeed: 0.0008, breatheAmount: 0.012, waveAmp: 2.0,  waveSpeed: 0.0010, waveFreq: 0.07 },
  failed:     { breatheSpeed: 0.0008, breatheAmount: 0.012, waveAmp: 2.0,  waveSpeed: 0.0010, waveFreq: 0.07 },
}

/* ═══════════════════════════════════════════════════════════════════════════
   Canvas / interaction constants
   ═══════════════════════════════════════════════════════════════════════════ */
const CANVAS_SIZE     = 200   // Internal canvas resolution (px)
const ORB_RENDER_SIZE = 150   // The orb image is drawn at this size, centred
const ORB_OFFSET      = (CANVAS_SIZE - ORB_RENDER_SIZE) / 2 // padding for dispersed particles
const DISPLAY_SIZE    = 72    // CSS display size (px)
const PARTICLE_STEP   = 2     // Sample every Nth pixel from the rendered orb image
const TARGET_FPS      = 30
const FRAME_INTERVAL  = 1000 / TARGET_FPS

// Cursor interaction physics
const INTERACTION_RADIUS  = 32   // canvas-space px
const REPULSION_STRENGTH  = 5.5
const SPRING_K            = 0.065
const DAMPING             = 0.90

// White-background removal threshold
const WHITE_THRESHOLD = 235

/* ═══════════════════════════════════════════════════════════════════════════
   Component props (unchanged from original)
   ═══════════════════════════════════════════════════════════════════════════ */
interface SaarthiOrbProps {
  runState: AgentRunState
  isPanelOpen: boolean
  onActivate: () => void
  userRequest?: string
  parsedIntent?: string
  presentation?: 'renderer' | 'native-overlay'
  onIdleOrbBoundsChange?: (bounds: OrbOverlayBounds | null) => void
}

export const SaarthiOrb: React.FC<SaarthiOrbProps> = ({
  runState,
  isPanelOpen,
  onActivate,
  userRequest = 'Find flights from Mumbai to Delhi for next Monday',
  parsedIntent = 'Search and compare one-way flights from Mumbai (BOM) to Delhi (DEL)',
  presentation = 'renderer',
  onIdleOrbBoundsChange,
}) => {
  const isListening  = runState === 'listening'
  const isProcessing = runState === 'processing'
  const isCentralActive = isListening || isProcessing

  // When panel is open or orb is in central active mode, the idle bottom-right orb is hidden
  const showIdleOrb = !isPanelOpen && !isCentralActive

  /* ─────────────────────────────────────────────────────────────────────────
     Canvas refs and state
     ───────────────────────────────────────────────────────────────────────── */
  const canvasRef      = useRef<HTMLCanvasElement>(null)
  const particlesRef   = useRef<Particle[]>([])
  const animFrameRef   = useRef<number>(0)
  const lastFrameRef   = useRef<number>(0)
  const readyRef       = useRef(false)
  const runStateRef    = useRef<AgentRunState>(runState)
  const mouseRef       = useRef({ x: 0, y: 0, active: false })
  const idleOrbRef     = useRef<HTMLDivElement>(null)

  // Keep a DOM anchor in the renderer so the native overlay can use the
  // exact same idle-orb position without changing BrowserViewport's bounds.
  useLayoutEffect(() => {
    if (!onIdleOrbBoundsChange) return
    const orb = idleOrbRef.current
    if (!orb || !showIdleOrb) {
      onIdleOrbBoundsChange(null)
      return
    }

    const reportBounds = () => {
      const rect = orb.getBoundingClientRect()
      onIdleOrbBoundsChange({
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
      })
    }

    reportBounds()
    const observer = new ResizeObserver(reportBounds)
    observer.observe(orb)
    if (orb.parentElement) observer.observe(orb.parentElement)
    window.addEventListener('resize', reportBounds)

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', reportBounds)
      onIdleOrbBoundsChange(null)
    }
  }, [onIdleOrbBoundsChange, showIdleOrb])

  // Keep runState in a ref so the animation loop always sees the latest value
  // without needing to restart the effect.
  useEffect(() => {
    runStateRef.current = runState
  }, [runState])

  /* ─────────────────────────────────────────────────────────────────────────
     Load reference image → sample particles (runs once on mount)
     ───────────────────────────────────────────────────────────────────────── */
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const img = new Image()
    img.crossOrigin = 'anonymous'

    img.onload = () => {
      // Draw the reference image onto an offscreen canvas to read its pixels
      const offscreen = document.createElement('canvas')
      offscreen.width  = ORB_RENDER_SIZE
      offscreen.height = ORB_RENDER_SIZE
      const offCtx = offscreen.getContext('2d', { willReadFrequently: true })!
      offCtx.drawImage(img, 0, 0, ORB_RENDER_SIZE, ORB_RENDER_SIZE)

      const imageData = offCtx.getImageData(0, 0, ORB_RENDER_SIZE, ORB_RENDER_SIZE)
      const pixels = imageData.data

      // Sample pixels and create particles, skipping white/near-white background
      const particles: Particle[] = []
      for (let py = 0; py < ORB_RENDER_SIZE; py += PARTICLE_STEP) {
        for (let px = 0; px < ORB_RENDER_SIZE; px += PARTICLE_STEP) {
          const i = (py * ORB_RENDER_SIZE + px) * 4
          const r = pixels[i]
          const g = pixels[i + 1]
          const b = pixels[i + 2]
          const a = pixels[i + 3]

          // Skip transparent pixels
          if (a < 15) continue
          // Skip white / near-white background pixels
          if (r > WHITE_THRESHOLD && g > WHITE_THRESHOLD && b > WHITE_THRESHOLD) continue

          // Map pixel coords into the full canvas (with offset for centering)
          const homeX = px + ORB_OFFSET
          const homeY = py + ORB_OFFSET

          particles.push({
            homeX, homeY,
            x: homeX, y: homeY,
            vx: 0, vy: 0,
            r, g, b, a,
            wavePhase: Math.random() * Math.PI * 2,
          })
        }
      }

      particlesRef.current = particles
      readyRef.current = true
    }

    img.onerror = () => {
      console.error('[SaarthiOrb] Failed to load /saarthi-orb.png')
    }

    img.src = '/saarthi-orb.png'
  }, [])

  /* ─────────────────────────────────────────────────────────────────────────
     Animation loop
     ───────────────────────────────────────────────────────────────────────── */
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!

    const animate = (timestamp: number) => {
      animFrameRef.current = requestAnimationFrame(animate)

      // Throttle to target FPS
      if (timestamp - lastFrameRef.current < FRAME_INTERVAL) return
      lastFrameRef.current = timestamp

      if (!readyRef.current) return

      const particles = particlesRef.current
      const mouse = mouseRef.current
      const params = STATE_PARAMS[runStateRef.current] || STATE_PARAMS.idle
      const time = timestamp
      const cx = CANVAS_SIZE / 2
      const cy = CANVAS_SIZE / 2

      ctx.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE)

      // Global breathing scale
      const breatheScale = 1 + Math.sin(time * params.breatheSpeed) * params.breatheAmount

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i]

        // ── 1. Wave displacement (organic flowing motion) ──────────────
        const waveX =
          Math.sin(p.homeY * params.waveFreq + time * params.waveSpeed + p.wavePhase) * params.waveAmp +
          Math.sin(p.homeY * params.waveFreq * 1.7 + time * params.waveSpeed * 0.6 + p.wavePhase * 0.5) * params.waveAmp * 0.4
        const waveY =
          Math.cos(p.homeX * params.waveFreq * 0.8 + time * params.waveSpeed * 0.7 + p.wavePhase) * params.waveAmp * 0.5

        // Target = home + wave, scaled from centre for breathing
        const rawTX = p.homeX + waveX
        const rawTY = p.homeY + waveY
        const targetX = cx + (rawTX - cx) * breatheScale
        const targetY = cy + (rawTY - cy) * breatheScale

        // ── 2. Cursor repulsion ────────────────────────────────────────
        if (mouse.active) {
          const dx = p.x - mouse.x
          const dy = p.y - mouse.y
          const distSq = dx * dx + dy * dy
          const radiusSq = INTERACTION_RADIUS * INTERACTION_RADIUS

          if (distSq < radiusSq && distSq > 0.01) {
            const dist = Math.sqrt(distSq)
            const force = ((INTERACTION_RADIUS - dist) / INTERACTION_RADIUS) * REPULSION_STRENGTH
            p.vx += (dx / dist) * force
            p.vy += (dy / dist) * force
          }
        }

        // ── 3. Spring force toward target ──────────────────────────────
        p.vx += (targetX - p.x) * SPRING_K
        p.vy += (targetY - p.y) * SPRING_K

        // ── 4. Damping ─────────────────────────────────────────────────
        p.vx *= DAMPING
        p.vy *= DAMPING

        // ── 5. Update position ─────────────────────────────────────────
        p.x += p.vx
        p.y += p.vy

        // ── 6. Draw particle ───────────────────────────────────────────
        // Dispersed particles fade slightly based on distance from target
        const dispDx = p.x - targetX
        const dispDy = p.y - targetY
        const dispDist = Math.sqrt(dispDx * dispDx + dispDy * dispDy)
        const alphaFade = Math.max(0.35, 1 - dispDist / (INTERACTION_RADIUS * 1.8))
        const finalAlpha = (p.a / 255) * alphaFade

        ctx.fillStyle = `rgba(${p.r},${p.g},${p.b},${finalAlpha})`
        ctx.fillRect(p.x, p.y, PARTICLE_STEP, PARTICLE_STEP)
      }
    }

    animFrameRef.current = requestAnimationFrame(animate)

    return () => {
      cancelAnimationFrame(animFrameRef.current)
    }
  }, [])

  /* ─────────────────────────────────────────────────────────────────────────
     Mouse handlers (canvas-coordinate conversion)
     ───────────────────────────────────────────────────────────────────────── */
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const scaleX = CANVAS_SIZE / rect.width
    const scaleY = CANVAS_SIZE / rect.height
    mouseRef.current = {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top)  * scaleY,
      active: true,
    }
  }, [])

  const handleMouseLeave = useCallback(() => {
    mouseRef.current = { ...mouseRef.current, active: false }
  }, [])

  /* ═══════════════════════════════════════════════════════════════════════════
     Render
     ═══════════════════════════════════════════════════════════════════════════ */
  return (
    <>
      {/* 1. Floating Idle Orb — Canvas Particle System (Bottom-Right) */}
      {showIdleOrb && (
        <div
          ref={idleOrbRef}
          className={presentation === 'native-overlay'
            ? 'absolute left-3 top-3 z-30 select-none animate-saarthi-float pointer-events-auto'
            : 'absolute bottom-6 right-6 z-30 select-none animate-saarthi-float pointer-events-auto'}
          aria-label="Activate SAARTHI AI Voice Assistant"
        >
          <div
            role="button"
            tabIndex={0}
            onClick={onActivate}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onActivate() }}
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
            title="Activate SAARTHI (Voice-First)"
            className="saarthi-orb-canvas group relative cursor-pointer focus:outline-none"
            style={{ width: DISPLAY_SIZE, height: DISPLAY_SIZE }}
          >
            <canvas
              ref={canvasRef}
              width={CANVAS_SIZE}
              height={CANVAS_SIZE}
              style={{
                width: DISPLAY_SIZE,
                height: DISPLAY_SIZE,
                display: 'block',
              }}
            />
            {/* Ambient glow behind orb */}
            <span
              className="absolute -inset-2 rounded-full pointer-events-none animate-saarthi-glow opacity-70"
              style={{
                background: 'radial-gradient(circle, rgba(56,189,248,0.18) 0%, rgba(30,58,138,0.08) 60%, transparent 100%)',
              }}
            />
          </div>
        </div>
      )}

      {/* 2. Central Activation Modal / Overlay (Listening & Processing States) — UNCHANGED */}
      {isCentralActive && (
        <div
          className="absolute inset-0 z-40 flex flex-col items-center justify-center bg-neutral-950/75 backdrop-blur-md select-none transition-all duration-300 pointer-events-auto"
          aria-live="polite"
        >
          {/* Centered Orb with Sonar Rings */}
          <div className="relative flex items-center justify-center">
            {/* Concentric Wave Rings */}
            <div
              className="absolute h-36 w-36 sm:h-44 sm:w-44 rounded-full border border-cyan-400/40 animate-saarthi-ring-1 pointer-events-none"
            />
            <div
              className="absolute h-36 w-36 sm:h-44 sm:w-44 rounded-full border border-blue-500/30 animate-saarthi-ring-2 pointer-events-none"
            />
            <div
              className="absolute h-36 w-36 sm:h-44 sm:w-44 rounded-full border border-indigo-500/20 animate-saarthi-ring-3 pointer-events-none"
            />

            {/* Central Spherical Orb Visual */}
            <div
              className="relative flex h-24 w-24 sm:h-28 sm:w-28 items-center justify-center rounded-full animate-saarthi-active-glow transition-all duration-500"
              style={{
                background: 'radial-gradient(circle at 35% 30%, #7dd3fc 0%, #38bdf8 18%, #1d4ed8 52%, #0c1a3e 82%, #020712 100%)',
                boxShadow: '0 0 45px rgba(56, 189, 248, 0.7), 0 0 85px rgba(37, 99, 235, 0.55), inset 0 0 28px rgba(224, 242, 254, 0.65)',
              }}
            >
              {/* Glass Glint Reflection */}
              <span
                className="absolute inset-0 rounded-full pointer-events-none"
                style={{
                  background: 'radial-gradient(circle at 30% 22%, rgba(255, 255, 255, 0.8) 0%, rgba(255, 255, 255, 0.2) 35%, transparent 65%)',
                }}
              />

              {/* Internal Swirl Pulse */}
              <span
                className="absolute inset-2 rounded-full opacity-70 animate-saarthi-swirl pointer-events-none"
                style={{
                  background: 'conic-gradient(from 45deg, transparent, rgba(125, 211, 252, 0.6), transparent, rgba(29, 78, 216, 0.5), transparent)',
                }}
              />

              {/* Internal Luminous Specks */}
              <span
                className="absolute w-1.5 h-1.5 rounded-full bg-white pointer-events-none"
                style={{
                  top: '25%',
                  left: '68%',
                  boxShadow: '0 0 8px #ffffff, 0 0 12px #38bdf8',
                  animation: 'saarthi-sparkle 2.2s ease-in-out infinite',
                }}
              />
              <span
                className="absolute w-1.5 h-1.5 rounded-full bg-cyan-200 pointer-events-none"
                style={{
                  top: '64%',
                  left: '32%',
                  boxShadow: '0 0 6px #7dd3fc',
                  animation: 'saarthi-sparkle 2.7s ease-in-out infinite 0.7s',
                }}
              />
              <span
                className="absolute w-1 h-1 rounded-full bg-cyan-100 pointer-events-none"
                style={{
                  top: '40%',
                  left: '48%',
                  boxShadow: '0 0 6px #bae6fd',
                  animation: 'saarthi-sparkle 1.8s ease-in-out infinite 1.2s',
                }}
              />

              {/* Center Subtle Symbol / Compass */}
              <Compass className="h-7 w-7 text-cyan-100/40 animate-pulse pointer-events-none" />
            </div>
          </div>

          {/* Status and Intent Caption */}
          <div className="mt-8 flex flex-col items-center text-center max-w-md px-6">
            {isListening && (
              <div className="flex flex-col items-center space-y-3">
                <div className="flex items-center gap-2">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-400" />
                  </span>
                  <span className="text-sm font-semibold tracking-wider text-cyan-200 uppercase font-mono text-[11px]">
                    Listening...
                  </span>
                </div>

                {/* Minimal Audio Wave Visualizer Bars */}
                <div className="flex items-center gap-1.5 h-7 py-1">
                  <span className="w-1 bg-gradient-to-t from-blue-500 to-cyan-300 rounded-full animate-saarthi-bar-1" />
                  <span className="w-1 bg-gradient-to-t from-blue-500 to-cyan-300 rounded-full animate-saarthi-bar-2" />
                  <span className="w-1 bg-gradient-to-t from-blue-500 to-cyan-300 rounded-full animate-saarthi-bar-3" />
                  <span className="w-1 bg-gradient-to-t from-blue-500 to-cyan-300 rounded-full animate-saarthi-bar-4" />
                  <span className="w-1 bg-gradient-to-t from-blue-500 to-cyan-300 rounded-full animate-saarthi-bar-5" />
                </div>

                <p className="text-xs text-neutral-400 font-sans tracking-normal">
                  Speak an objective for SAARTHI to execute in the browser.
                </p>
              </div>
            )}

            {isProcessing && (
              <div className="flex flex-col items-center space-y-3">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-400" />
                  <span className="text-[11px] font-semibold tracking-wider text-cyan-200 uppercase font-mono">
                    Understanding Request...
                  </span>
                </div>

                {/* Voice Request & Parsed Intent Pill */}
                <div className="w-full rounded-xl border border-cyan-500/30 bg-neutral-900/90 p-3 shadow-lg shadow-cyan-950/40 text-left space-y-2">
                  <div>
                    <span className="text-[9px] font-mono uppercase tracking-[0.14em] text-neutral-500">
                      Request
                    </span>
                    <p className="text-xs font-medium text-white">
                      &quot;{userRequest}&quot;
                    </p>
                  </div>
                  <div className="pt-2 border-t border-neutral-800">
                    <span className="text-[9px] font-mono uppercase tracking-[0.14em] text-cyan-400">
                      Intent
                    </span>
                    <p className="text-xs text-cyan-200/90 font-sans">
                      {parsedIntent}
                    </p>
                  </div>
                </div>

                <p className="text-[11px] text-neutral-400 font-mono tracking-tight flex items-center gap-1.5">
                  <Sparkles className="h-3 w-3 text-cyan-400" />
                  Transitioning to execution...
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
