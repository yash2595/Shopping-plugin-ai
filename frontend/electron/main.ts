import { app, BrowserWindow, WebContentsView, ipcMain } from 'electron'
import * as path from 'path'
import * as fs from 'fs'

// Keep Electron's runtime profile in the writable project workspace. The host
// profile can be access-denied in managed environments, which breaks Chromium
// cache/GPU startup before the renderer can paint the overlay.
app.setPath('userData', path.join(process.cwd(), '.electron-runtime'))
app.disableHardwareAcceleration()
app.name = 'Copilot Browser'

let mainWindow: BrowserWindow | null = null
const views = new Map<string, WebContentsView>()
let activeTabId: string = 'tab-1'
let currentBounds = { x: 0, y: 0, width: 0, height: 0 }
let orbOverlayWindow: BrowserWindow | null = null
let orbOverlayReady = false
let orbOverlayVisible = false
let orbOverlayBounds: { x: number; y: number; width: number; height: number } | null = null
let orbOverlayState = 'idle'
let orbOverlayFocused = false
let orbOverlayAnimationTimer: NodeJS.Timeout | null = null
let orbOverlayReturnTimer: NodeJS.Timeout | null = null
let lastOrbOverlayScreenBounds: { x: number; y: number; width: number; height: number } | null = null

const ORB_OVERLAY_SIZE = 96
const ORB_FOCUSED_SIZE = 280
const ORB_OVERLAY_INSET = 0
const ORB_FOCUSED_TOP_MARGIN = 150
const DEV_RENDERER_URL = 'http://localhost:7319'

function loadRendererPage(window: BrowserWindow, devPath: string, productionFile: string, label: string) {
  let fallbackStarted = false
  const loadProductionFallback = () => {
    if (fallbackStarted || window.isDestroyed() || !fs.existsSync(productionFile)) return
    fallbackStarted = true
    console.warn(`[${label}] Dev server unavailable; loading ${productionFile}`)
    window.loadFile(productionFile).catch((error) => {
      console.error(`[${label}] Production renderer load failed: ${String(error)}`)
    })
  }

  window.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || !validatedURL.startsWith(DEV_RENDERER_URL)) return
    console.error(`[${label}] Dev renderer load failed (${errorCode}): ${errorDescription} ${validatedURL}`)
    loadProductionFallback()
  })

  window.loadURL(`${DEV_RENDERER_URL}${devPath}`).catch(() => loadProductionFallback())
}

// Enable Chrome DevTools Protocol for later automation work. Keeping the
// default stable also allows a separate local test instance to avoid a port
// already used by another Electron session.
app.commandLine.appendSwitch(
  'remote-debugging-port',
  process.env.SAARTHI_REMOTE_DEBUGGING_PORT || '9222',
)

// On Windows (Intel Iris Xe / certain AMD/NVIDIA configs) the Chromium GPU
// process sandbox fails to initialise and exits immediately with
// exit_code=-1073741515 (0xC0000135 – STATUS_DLL_NOT_FOUND).  Disabling the
// GPU sandbox fixes the crash while keeping hardware-accelerated rendering.
// This is NOT the same as `--disable-gpu`; full GPU acceleration is retained.
app.commandLine.appendSwitch('disable-gpu-sandbox')
// This managed host cannot launch Chromium renderer sandboxes (exit code 49).
// Keep the existing WebContentsView/overlay architecture but allow renderers
// to start reliably in this environment.
app.commandLine.appendSwitch('no-sandbox')

const KNOWN_DOMAINS: Record<string, string> = {
  youtube: 'https://www.youtube.com',
  google: 'https://www.google.com',
  wikipedia: 'https://www.wikipedia.org',
  github: 'https://github.com',
  reddit: 'https://www.reddit.com',
  twitter: 'https://x.com',
  x: 'https://x.com',
  amazon: 'https://www.amazon.com',
  hackernews: 'https://news.ycombinator.com',
  'hacker news': 'https://news.ycombinator.com',
}

const COMMON_TLDS = [
  '.com',
  '.org',
  '.net',
  '.io',
  '.ai',
  '.in',
  '.co',
  '.co.in',
  '.co.uk',
  '.edu',
  '.gov',
  '.dev',
  '.app',
]

function normalizeUrl(raw: string): string {
  let text = (raw || '').trim()
  if (!text) return 'https://www.google.com'

  const lower = text.toLowerCase()
  const prefixes = [
    'open url ',
    'open link ',
    'open website ',
    'open site ',
    'open page ',
    'open ',
    'navigate to ',
    'go to ',
    'visit ',
    'browse to ',
    'browse ',
  ]

  for (const prefix of prefixes) {
    if (lower.startsWith(prefix)) {
      text = text.slice(prefix.length).trim().replace(/^["']|["']$/g, '')
      break
    }
  }

  if (text.startsWith('http://') || text.startsWith('https://')) {
    return text
  }

  const cleanLower = text.toLowerCase().trim()
  if (KNOWN_DOMAINS[cleanLower]) {
    return KNOWN_DOMAINS[cleanLower]
  }

  const hasTld = COMMON_TLDS.some((tld) => cleanLower.includes(tld))
  if (hasTld && !cleanLower.includes(' ')) {
    return `https://${text}`
  }

  if (/^[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(\/.*)?$/.test(cleanLower)) {
    return `https://${text}`
  }

  return `https://www.google.com/search?q=${encodeURIComponent(text)}`
}

function createTabView(tabId: string, rawUrl: string): WebContentsView {
  const view = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
    },
  })

  view.setBounds(currentBounds)

  // Handle window.open & target="_blank" -> route into SAARTHI native browser tab
  view.webContents.setWindowOpenHandler(({ url }) => {
    console.log(`[WebContentsView:${tabId}] Intercepted target="_blank" window.open: ${url}`)
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('browser:new-tab-requested', { url })
    }
    return { action: 'deny' }
  })

  const sendNavState = () => {
    if (!mainWindow || mainWindow.isDestroyed() || view.webContents.isDestroyed()) return
    const url = view.webContents.getURL()
    const nav = view.webContents.navigationHistory
    const canGoBack = nav ? nav.canGoBack() : false
    const canGoForward = nav ? nav.canGoForward() : false

    mainWindow.webContents.send('browser:nav-state', { tabId, canGoBack, canGoForward })
    if (url && url !== 'about:blank') {
      mainWindow.webContents.send('browser:url-change', { tabId, url })
    }
  }

  view.webContents.on('did-start-loading', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('browser:loading', { tabId, isLoading: true })
    }
  })

  view.webContents.on('did-stop-loading', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('browser:loading', { tabId, isLoading: false })
      sendNavState()
    }
  })

  view.webContents.on('did-navigate', (_e, navUrl) => {
    console.log(`[WebContentsView:${tabId}] Loaded: ${navUrl}`)
    sendNavState()
  })

  view.webContents.on('did-navigate-in-page', (_e, navUrl) => {
    console.log(`[WebContentsView:${tabId}] In-page navigation: ${navUrl}`)
    sendNavState()
  })

  view.webContents.on('page-title-updated', (_event, title) => {
    console.log(`[WebContentsView:${tabId}] Title updated: ${title}`)
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('browser:title-change', { tabId, title })
    }
  })

  views.set(tabId, view)

  const finalUrl = normalizeUrl(rawUrl)
  console.log(`[WebContentsView:${tabId}] Loading initial URL: ${finalUrl}`)
  view.webContents.loadURL(finalUrl)

  return view
}

function switchActiveTab(tabId: string) {
  if (!mainWindow || !views.has(tabId)) return

  // Detach previous active view if mounted
  if (activeTabId && views.has(activeTabId) && activeTabId !== tabId) {
    const oldView = views.get(activeTabId)!
    try {
      mainWindow.contentView.removeChildView(oldView)
    } catch {}
  }

  activeTabId = tabId
  const targetView = views.get(tabId)!
  targetView.setBounds(currentBounds)

  try {
    mainWindow.contentView.addChildView(targetView)
  } catch {}

  // Sync state with UI
  const url = targetView.webContents.getURL()
  const title = targetView.webContents.getTitle()
  const nav = targetView.webContents.navigationHistory

  mainWindow.webContents.send('browser:nav-state', {
    tabId,
    canGoBack: nav ? nav.canGoBack() : false,
    canGoForward: nav ? nav.canGoForward() : false,
  })
  if (url && url !== 'about:blank') {
    mainWindow.webContents.send('browser:url-change', { tabId, url })
  }
  if (title) {
    mainWindow.webContents.send('browser:title-change', { tabId, title })
  }
}

function closeTabView(tabId: string) {
  if (!views.has(tabId)) return
  const viewToClose = views.get(tabId)!

  if (activeTabId === tabId && mainWindow) {
    try {
      mainWindow.contentView.removeChildView(viewToClose)
    } catch {}
  }

  try {
    viewToClose.webContents.close()
  } catch {}

  views.delete(tabId)
  console.log(`[WebContentsView:${tabId}] Safely closed and destroyed. Remaining tabs: ${views.size}`)
}

function getBrowserView(tabId?: string) {
  const targetId = tabId || activeTabId
  return views.get(targetId)
}

function waitForBrowserStop(view: WebContentsView, timeout = 15000) {
  return new Promise<void>((resolve, reject) => {
    let settled = false
    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      view.webContents.removeListener('did-stop-loading', onStop)
      view.webContents.removeListener('did-fail-load', onFail)
      error ? reject(error) : resolve()
    }
    const onStop = () => finish()
    const onFail = (_event: Electron.Event, errorCode: number, description: string, _url: string, isMainFrame: boolean) => {
      if (isMainFrame && errorCode !== -3) finish(new Error(description))
    }
    const timer = setTimeout(() => finish(new Error('Browser action timed out')), timeout)
    view.webContents.once('did-stop-loading', onStop)
    view.webContents.on('did-fail-load', onFail)
  })
}

async function focusBrowserSearch(tabId?: string) {
  const view = getBrowserView(tabId)
  if (!view) throw new Error('Active browser tab is unavailable')
  view.webContents.focus()
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const focused = await view.webContents.executeJavaScript(`(() => {
      const field = document.querySelector('textarea[name="q"], input[name="q"], textarea[aria-label*="Search" i], input[aria-label*="Search" i]');
      if (!field) return false;
      field.focus();
      return true;
    })()`)
    if (focused) return
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error('Google search field was not found')
}

async function typeBrowserText(text: string, tabId?: string) {
  const view = getBrowserView(tabId)
  if (!view) throw new Error('Active browser tab is unavailable')
  view.webContents.focus()
  for (const character of text) {
    try {
      view.webContents.sendInputEvent({ type: 'char', keyCode: character })
    } catch (error) {
      console.error('[DemoTyping] Failed to send character input', { character, error })
      throw error
    }
    await new Promise((resolve) => setTimeout(resolve, 65))
  }
}

async function submitBrowserSearch(tabId?: string) {
  const view = getBrowserView(tabId)
  if (!view) throw new Error('Active browser tab is unavailable')
  view.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter' })
  view.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter' })
  await waitForBrowserStop(view)
}

async function collectBrowserResults(tabId?: string) {
  const view = getBrowserView(tabId)
  if (!view) throw new Error('Active browser tab is unavailable')
  const payload = await view.webContents.executeJavaScript(`(async () => {
    const seen = new Set();
    const diagnostics = [];
    const results = [];
    const summary = { totalAnchors: 0, httpCandidates: 0, googleRejected: 0, adRejected: 0, hiddenRejected: 0, duplicateRejected: 0, textRejected: 0 };
    const resolveDestination = async (raw) => {
      if (!/((^|\\.)google\\.)|accounts\\.google\\./i.test(raw.hostname)) return raw;
      if (/^\\/(url|aclk)$/i.test(raw.pathname)) {
        const candidate = raw.searchParams.get('q') || raw.searchParams.get('url') || raw.searchParams.get('adurl');
        if (candidate) { try { return new URL(candidate, location.href); } catch {} }
      }
      if (/^\\/goto$/i.test(raw.pathname)) {
        try {
          const response = await fetch(raw.href, { credentials: 'include' });
          const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
          const target = [...doc.querySelectorAll('a[href]')].map((link) => {
            try { return new URL(link.href, raw.href); } catch { return null; }
          }).find((url) => url && /^https?:$/.test(url.protocol) && !/((^|\\.)google\\.)|accounts\\.google\\./i.test(url.hostname));
          if (target) return target;
        } catch {}
      }
      return raw;
    };
    for (const anchor of [...document.querySelectorAll('a[href]')]) {
      summary.totalAnchors += 1;
      const raw = new URL(anchor.href, location.href);
      const destination = await resolveDestination(raw);
      const redirect = /((^|\\.)google\\.)|accounts\\.google\\./i.test(raw.hostname)
        && /^\\/(url|aclk|goto)$/i.test(raw.pathname);
      const text = (anchor.innerText || anchor.getAttribute('aria-label') || anchor.textContent || '').replace(/\\s+/g, ' ').trim();
      const rect = anchor.getBoundingClientRect();
      const style = getComputedStyle(anchor);
      const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      const sponsored = !!anchor.closest('[data-text-ad], [aria-label*="sponsored" i], [aria-label*="advertisement" i]');
      const unresolvedGoto = /^\\/goto$/i.test(raw.pathname) && destination.href === raw.href;
      const internal = !/^https?:$/.test(destination.protocol)
        || (/((^|\\.)google\\.)|accounts\\.google\\./i.test(destination.hostname) && !unresolvedGoto)
        || /^(gstatic|googleusercontent)\\./i.test(destination.hostname);
      if (/^https?:$/.test(destination.protocol)) summary.httpCandidates += 1;
      let reason = '';
      if (internal) { reason = 'internal/non-http destination'; summary.googleRejected += 1; }
      else if (!visible) { reason = 'hidden'; summary.hiddenRejected += 1; }
      else if (text.length < 8) { reason = 'insufficient text'; summary.textRejected += 1; }
      else if (sponsored) { reason = 'sponsored/ad container'; summary.adRejected += 1; }
      else if (seen.has(destination.href)) { reason = 'duplicate'; summary.duplicateRejected += 1; }
      const item = {
        tag: anchor.tagName,
        text: text.slice(0, 140),
        href: anchor.getAttribute('href') || '',
        resolved: destination.href,
        redirect,
        visible,
        external: /^https?:$/.test(destination.protocol) && !internal,
        googleInternal: internal,
        sponsored,
        duplicate: seen.has(destination.href),
        accepted: !reason,
        reason: reason || 'accepted',
      };
      diagnostics.push(item);
      if (!reason) { seen.add(destination.href); results.push({ href: destination.href, text: item.text }); }
    }
    return { results: results.slice(0, 10), diagnostics: diagnostics.slice(0, 30), summary };
  })()`)
  console.warn('=== GOOGLE RESULT PARSER DIAGNOSTIC ===')
  console.warn('Total anchors:', payload.summary?.totalAnchors)
  console.warn('HTTP/HTTPS candidates:', payload.summary?.httpCandidates)
  console.warn('Google/internal rejected:', payload.summary?.googleRejected)
  console.warn('Ad/sponsored rejected:', payload.summary?.adRejected)
  console.warn('Hidden rejected:', payload.summary?.hiddenRejected)
  console.warn('Duplicate rejected:', payload.summary?.duplicateRejected)
  console.warn('Text/content rejected:', payload.summary?.textRejected)
  console.warn('Final usable results:', payload.results)
  payload.diagnostics?.forEach((candidate: unknown, index: number) => console.warn(`Candidate #${index + 1}`, candidate))
  return payload.results
}

async function openBrowserResult(index: number, tabId?: string) {
  const view = getBrowserView(tabId)
  if (!view) throw new Error('Active browser tab is unavailable')
  const opened = await view.webContents.executeJavaScript(`(async () => {
    const seen = new Set();
    const resolveDestination = async (raw) => {
      if (!/((^|\\.)google\\.)|accounts\\.google\\./i.test(raw.hostname)) return raw;
      if (/^\\/(url|aclk)$/i.test(raw.pathname)) {
        const candidate = raw.searchParams.get('q') || raw.searchParams.get('url') || raw.searchParams.get('adurl');
        if (candidate) { try { return new URL(candidate, location.href); } catch {} }
      }
      if (/^\\/goto$/i.test(raw.pathname)) {
        try {
          const response = await fetch(raw.href, { credentials: 'include' });
          const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
          const target = [...doc.querySelectorAll('a[href]')].map((link) => {
            try { return new URL(link.href, raw.href); } catch { return null; }
          }).find((url) => url && /^https?:$/.test(url.protocol) && !/((^|\\.)google\\.)|accounts\\.google\\./i.test(url.hostname));
          if (target) return target;
        } catch {}
      }
      return raw;
    };
    const anchors = [...document.querySelectorAll('a[href]')].map((anchor) => {
      const raw = new URL(anchor.href, location.href);
      const text = (anchor.innerText || anchor.getAttribute('aria-label') || anchor.textContent || '').replace(/\\s+/g, ' ').trim();
      const rect = anchor.getBoundingClientRect();
      const style = getComputedStyle(anchor);
      const sponsored = !!anchor.closest('[data-text-ad], [aria-label*="sponsored" i], [aria-label*="advertisement" i]');
      return { anchor, raw, text, sponsored, visible: rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' };
    });
    const normalized = [];
    for (const item of anchors) normalized.push({ ...item, destination: await resolveDestination(item.raw) });
    const usable = normalized.filter((item) => item && /^https?:$/.test(item.destination.protocol)
      && (!/((^|\\.)google\\.)|accounts\\.google\\./i.test(item.destination.hostname)
        || (/^\\/goto$/i.test(item.raw.pathname) && item.destination.href === item.raw.href))
      && !/^(gstatic|googleusercontent)\\./i.test(item.destination.hostname)
      && !item.sponsored && item.visible && item.text.length >= 8
      && !seen.has(item.destination.href) && (seen.add(item.destination.href), true));
    const target = usable[${Math.max(0, index - 1)}];
    if (!target) return false;
    target.click();
    return true;
  })()`)
  if (!opened) throw new Error('Requested browser result was unavailable')
  await waitForBrowserStop(view)
}

function hideOrbOverlay() {
  stopOrbOverlayAnimation()
  if (orbOverlayReturnTimer) {
    clearTimeout(orbOverlayReturnTimer)
    orbOverlayReturnTimer = null
  }
  orbOverlayVisible = false
  if (orbOverlayWindow && !orbOverlayWindow.isDestroyed()) {
    orbOverlayWindow.setIgnoreMouseEvents(true, { forward: true })
    orbOverlayWindow.hide()
  }
}

function stopOrbOverlayAnimation() {
  if (orbOverlayAnimationTimer) {
    clearTimeout(orbOverlayAnimationTimer)
    orbOverlayAnimationTimer = null
  }
}

function getOrbOverlayTargetBounds() {
  if (!mainWindow || mainWindow.isDestroyed() || !orbOverlayBounds) return null
  const contentBounds = mainWindow.getContentBounds()
  if (orbOverlayFocused) {
    return {
      x: contentBounds.x + Math.round((contentBounds.width - ORB_FOCUSED_SIZE) / 2),
      y: contentBounds.y + ORB_FOCUSED_TOP_MARGIN,
      width: ORB_FOCUSED_SIZE,
      height: ORB_FOCUSED_SIZE,
    }
  }
  return {
    x: contentBounds.x + Math.round(orbOverlayBounds.x) - ORB_OVERLAY_INSET,
    y: contentBounds.y + Math.round(orbOverlayBounds.y) - ORB_OVERLAY_INSET,
    width: ORB_OVERLAY_SIZE,
    height: ORB_OVERLAY_SIZE,
  }
}

function applyOrbOverlayBounds(bounds: { x: number; y: number; width: number; height: number }) {
  const overlay = ensureOrbOverlay()
  const previous = lastOrbOverlayScreenBounds
  const unchanged = previous
    && previous.x === bounds.x && previous.y === bounds.y
    && previous.width === bounds.width && previous.height === bounds.height
  if (!unchanged) {
    overlay.setBounds(bounds)
    lastOrbOverlayScreenBounds = bounds
  }
  if (orbOverlayReady && !overlay.isVisible()) overlay.showInactive()
}

function animateOrbOverlayToTarget(onComplete?: () => void) {
  if (!orbOverlayVisible) return
  const target = getOrbOverlayTargetBounds()
  if (!target) return
  const overlay = ensureOrbOverlay()
  const start = overlay.getBounds()
  stopOrbOverlayAnimation()
  const startedAt = Date.now()
  const duration = 750
  const tick = () => {
    if (!orbOverlayWindow || orbOverlayWindow.isDestroyed() || !orbOverlayVisible) return
    const nextTarget = getOrbOverlayTargetBounds()
    if (!nextTarget) return
    const progress = Math.min(1, (Date.now() - startedAt) / duration)
    const eased = progress * progress * (3 - 2 * progress)
    applyOrbOverlayBounds({
      x: Math.round(start.x + (nextTarget.x - start.x) * eased),
      y: Math.round(start.y + (nextTarget.y - start.y) * eased),
      width: Math.round(start.width + (nextTarget.width - start.width) * eased),
      height: Math.round(start.height + (nextTarget.height - start.height) * eased),
    })
    if (progress < 1) orbOverlayAnimationTimer = setTimeout(tick, 16)
    else {
      orbOverlayAnimationTimer = null
      onComplete?.()
    }
  }
  tick()
}

function syncOrbOverlayBounds() {
  if (!mainWindow || mainWindow.isDestroyed() || !orbOverlayBounds || !orbOverlayVisible) return
  if (orbOverlayFocused || orbOverlayAnimationTimer) {
    animateOrbOverlayToTarget()
    return
  }
  const target = getOrbOverlayTargetBounds()
  if (target) applyOrbOverlayBounds(target)
}

function syncOrbOverlayState() {
  if (orbOverlayWindow && !orbOverlayWindow.isDestroyed() && orbOverlayReady) {
    orbOverlayWindow.webContents.send('orb:overlay-state', orbOverlayState)
  }
}

function syncOrbOverlayMode() {
  if (orbOverlayWindow && !orbOverlayWindow.isDestroyed() && orbOverlayReady) {
    orbOverlayWindow.webContents.send('orb:overlay-mode', orbOverlayFocused ? 'focused' : 'corner')
  }
}

function ensureOrbOverlay(): BrowserWindow {
  if (orbOverlayWindow && !orbOverlayWindow.isDestroyed()) return orbOverlayWindow
  if (!mainWindow || mainWindow.isDestroyed()) {
    throw new Error('Cannot create the SAARTHI orb overlay without a main window')
  }

  orbOverlayReady = false
  orbOverlayWindow = new BrowserWindow({
    parent: mainWindow,
    width: ORB_OVERLAY_SIZE,
    height: ORB_OVERLAY_SIZE,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  const overlay = orbOverlayWindow
  // The overlay window is already tightly bounded to the avatar region, so
  // keep that region interactive instead of relying on a click-through
  // pointer handshake that can strand the avatar in ignore mode.
  overlay.setIgnoreMouseEvents(false)
  overlay.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  const markReady = (source: string) => {
    if (overlay.isDestroyed() || orbOverlayWindow !== overlay) return
    orbOverlayReady = true
    console.log(`[OrbOverlay] Ready via ${source}: ${overlay.getURL()}`)
    syncOrbOverlayState()
    syncOrbOverlayMode()
    syncOrbOverlayBounds()
  }
  overlay.once('ready-to-show', () => markReady('ready-to-show'))
  overlay.webContents.on('did-finish-load', () => markReady('did-finish-load'))
  overlay.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (isMainFrame) console.error(`[OrbOverlay] Load failed (${errorCode}): ${errorDescription} ${validatedURL}`)
  })
  overlay.webContents.on('render-process-gone', (_event, details) => {
    orbOverlayReady = false
    console.error(`[OrbOverlay] Renderer gone: ${details.reason} ${details.exitCode}`)
  })
  overlay.webContents.on('destroyed', () => {
    orbOverlayReady = false
    console.log('[OrbOverlay] WebContents destroyed')
  })
  overlay.on('closed', () => {
    console.log('[OrbOverlay] BrowserWindow closed')
    if (orbOverlayWindow === overlay) {
      orbOverlayWindow = null
      orbOverlayReady = false
    }
  })
  loadRendererPage(overlay, '/orb-overlay.html', path.join(process.cwd(), 'dist', 'orb-overlay.html'), 'OrbOverlay')

  return overlay
}

function disposeOrbOverlay() {
  stopOrbOverlayAnimation()
  if (orbOverlayReturnTimer) clearTimeout(orbOverlayReturnTimer)
  const overlay = orbOverlayWindow
  orbOverlayWindow = null
  orbOverlayReady = false
  orbOverlayVisible = false
  orbOverlayBounds = null
  orbOverlayFocused = false
  orbOverlayReturnTimer = null
  lastOrbOverlayScreenBounds = null
  if (overlay && !overlay.isDestroyed()) overlay.destroy()
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    title: 'Copilot Browser',
    backgroundColor: '#0a0a0a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // Start the WebContentsView with zero bounds (hidden).  Once the React
  // renderer loads and BrowserViewport mounts, its useLayoutEffect will
  // measure its actual DOM rect and send it via the 'browser:update-bounds'
  // IPC, at which point the native view is positioned to match exactly.
  // This avoids covering the React browser chrome (tabs, toolbar, sidebar).
  currentBounds = { x: 0, y: 0, width: 0, height: 0 }

  // Create initial tab-1 WebContentsView
  const initialView = createTabView('tab-1', 'https://www.google.com')
  activeTabId = 'tab-1'
  mainWindow.contentView.addChildView(initialView)

  mainWindow.on('closed', () => {
    disposeOrbOverlay()
    mainWindow = null
    views.clear()
  })

  mainWindow.on('move', syncOrbOverlayBounds)
  mainWindow.on('resize', syncOrbOverlayBounds)
  mainWindow.on('minimize', hideOrbOverlay)
  mainWindow.on('restore', () => {
    if (orbOverlayBounds) {
      orbOverlayVisible = true
      syncOrbOverlayBounds()
    }
  })
  mainWindow.webContents.on('did-finish-load', () => console.log('[MainRenderer] did-finish-load'))
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    console.error(`[MainRenderer] Renderer gone: ${details.reason} ${details.exitCode}`)
  })

  loadRendererPage(mainWindow, '/', path.join(process.cwd(), 'dist', 'index.html'), 'MainRenderer')

  // Create the overlay independently of the React bounds handshake so its
  // renderer/target exists even if the main renderer is still recovering.
  ensureOrbOverlay()
}

// IPC Handlers for React Frontend Multi-Tab & Browser Controls
ipcMain.on('browser:create-tab', (_event, payload: { tabId: string; url: string; activate?: boolean }) => {
  const { tabId, url, activate = true } = payload
  if (!views.has(tabId)) {
    createTabView(tabId, url || 'https://www.google.com')
  }
  if (activate) {
    switchActiveTab(tabId)
  }
})

ipcMain.on('browser:switch-tab', (_event, payload: { tabId: string }) => {
  switchActiveTab(payload.tabId)
})

ipcMain.on('browser:close-tab', (_event, payload: { tabId: string }) => {
  closeTabView(payload.tabId)
})

ipcMain.on('browser:update-bounds', (_event, bounds) => {
  if (!bounds) return
  currentBounds = {
    x: Math.round(bounds.x),
    y: Math.round(bounds.y),
    width: Math.max(10, Math.round(bounds.width)),
    height: Math.max(10, Math.round(bounds.height)),
  }

  if (views.has(activeTabId)) {
    views.get(activeTabId)!.setBounds(currentBounds)
  }
})

ipcMain.on('orb:update-overlay', (event, bounds: { x: number; y: number; width: number; height: number } | null) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return
  if (!bounds || !Number.isFinite(bounds.x) || !Number.isFinite(bounds.y) || !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)) {
    if (orbOverlayFocused) return
    orbOverlayBounds = null
    hideOrbOverlay()
    return
  }

  orbOverlayBounds = {
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
  }
  orbOverlayVisible = true
  syncOrbOverlayBounds()
})

ipcMain.on('orb:activate', (event) => {
  if (!mainWindow || !orbOverlayWindow || event.sender !== orbOverlayWindow.webContents) return
  orbOverlayFocused = true
  orbOverlayVisible = true
  syncOrbOverlayMode()
  animateOrbOverlayToTarget()
  mainWindow.webContents.send('orb:activated')
})

ipcMain.on('orb:pointer-inside', (event, isInside: boolean) => {
  if (!orbOverlayWindow || event.sender !== orbOverlayWindow.webContents) return
  // The native overlay is bounded to the avatar region; keep it interactive
  // so the avatar button can always receive the existing activation click.
  void isInside
})

ipcMain.on('orb:speaking', (event, speaking: boolean) => {
  if (!orbOverlayWindow || event.sender !== mainWindow?.webContents || orbOverlayWindow.isDestroyed()) return
  orbOverlayWindow.webContents.send('orb:overlay-speaking', Boolean(speaking))
})

ipcMain.on('orb:update-state', (event, payload: { state: string; isPanelOpen: boolean }) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return
  orbOverlayState = payload?.state || 'idle'
  const activeState = orbOverlayState === 'listening' || orbOverlayState === 'processing' || orbOverlayState === 'executing'
  if (activeState) {
    if (orbOverlayReturnTimer) {
      clearTimeout(orbOverlayReturnTimer)
      orbOverlayReturnTimer = null
    }
    orbOverlayFocused = true
    orbOverlayVisible = true
    syncOrbOverlayMode()
    syncOrbOverlayState()
    syncOrbOverlayBounds()
    return
  }
  if (orbOverlayState === 'completed' || orbOverlayState === 'failed') {
    orbOverlayFocused = true
    orbOverlayVisible = true
    syncOrbOverlayMode()
    syncOrbOverlayState()
    syncOrbOverlayBounds()
    if (orbOverlayReturnTimer) clearTimeout(orbOverlayReturnTimer)
    orbOverlayReturnTimer = setTimeout(() => {
      orbOverlayReturnTimer = null
      if (orbOverlayState !== 'completed' && orbOverlayState !== 'failed') return
      orbOverlayFocused = false
      syncOrbOverlayMode()
      animateOrbOverlayToTarget(() => {
        if (orbOverlayState === 'completed' || orbOverlayState === 'failed') {
          orbOverlayState = 'idle'
          syncOrbOverlayState()
        }
      })
    }, 1800)
    return
  }
  orbOverlayFocused = false
  syncOrbOverlayMode()
  if (payload?.isPanelOpen && orbOverlayState === 'idle') {
    hideOrbOverlay()
    return
  }
  if (orbOverlayBounds) {
    orbOverlayVisible = true
    syncOrbOverlayState()
    syncOrbOverlayBounds()
  }
})

ipcMain.on('browser:navigate', (_event, payload: { url: string; tabId?: string } | string) => {
  const rawUrl = typeof payload === 'string' ? payload : payload.url
  const targetId = (typeof payload === 'object' && payload.tabId) || activeTabId
  const targetView = views.get(targetId)
  if (!targetView) return

  const finalUrl = normalizeUrl(rawUrl)
  console.log(`[WebContentsView:${targetId}] Navigating to: ${finalUrl}`)
  targetView.webContents.loadURL(finalUrl)
})

ipcMain.on('browser:go-back', (_event, payload?: { tabId?: string }) => {
  const targetId = payload?.tabId || activeTabId
  const targetView = views.get(targetId)
  if (!targetView) return

  const nav = targetView.webContents.navigationHistory
  if (nav && nav.canGoBack()) {
    nav.goBack()
  }
})

ipcMain.on('browser:go-forward', (_event, payload?: { tabId?: string }) => {
  const targetId = payload?.tabId || activeTabId
  const targetView = views.get(targetId)
  if (!targetView) return

  const nav = targetView.webContents.navigationHistory
  if (nav && nav.canGoForward()) {
    nav.goForward()
  }
})

ipcMain.on('browser:reload', (_event, payload?: { tabId?: string }) => {
  const targetId = payload?.tabId || activeTabId
  const targetView = views.get(targetId)
  if (targetView) {
    targetView.webContents.reload()
  }
})

ipcMain.handle('browser:demo-focus-search', (_event, tabId?: string) => focusBrowserSearch(tabId))
ipcMain.handle('browser:demo-type', async (_event, text: string, tabId?: string) => {
  try {
    return await typeBrowserText(text, tabId)
  } catch (error) {
    console.error('[IPC browser:demo-type] failed', error)
    throw error
  }
})
ipcMain.handle('browser:demo-submit-search', (_event, tabId?: string) => submitBrowserSearch(tabId))
ipcMain.handle('browser:demo-collect-results', (_event, tabId?: string) => collectBrowserResults(tabId))
ipcMain.handle('browser:demo-open-result', (_event, index: number, tabId?: string) => openBrowserResult(index, tabId))

// Secure In-Memory Auth Vault (Keeps tokens safe in main process without leaking to disk/localStorage)
let secureAuthToken: string | null = null

ipcMain.handle('auth:get-token', () => secureAuthToken)
ipcMain.handle('auth:set-token', (_event, token: string) => {
  secureAuthToken = typeof token === 'string' && token.trim().length > 0 ? token.trim() : null
  return true
})
ipcMain.handle('auth:clear-token', () => {
  secureAuthToken = null
  return true
})

// Safe Snapshot Extraction from Active Chromium WebContentsView
ipcMain.handle('browser:extract-snapshot', async (_event, tabId?: string) => {
  const targetId = tabId || activeTabId
  const targetView = views.get(targetId)
  if (!targetView || targetView.webContents.isDestroyed()) {
    return { success: false, error: 'No active web view available' }
  }

  try {
    const rawData = await targetView.webContents.executeJavaScript(`
      (() => {
        try {
          const url = window.location.href;
          const hostname = window.location.hostname || '';
          const title = (document.title || '').trim();
          
          let retailer = 'unknown';
          if (hostname.includes('amazon')) {
            retailer = 'amazon_in';
          } else if (hostname.includes('flipkart')) {
            retailer = 'flipkart';
          }

          let retailerProductId = '';
          const asinMatch = url.match(/(?:\\/dp\\/|\\/gp\\/product\\/|\\/ASIN\\/)([A-Z0-9]{10})/i);
          if (asinMatch) {
            retailerProductId = asinMatch[1];
          } else {
            const flipkartMatch = url.match(/\\/p\\/(itm[a-zA-Z0-9]+)/i);
            if (flipkartMatch) {
              retailerProductId = flipkartMatch[1];
            } else {
              retailerProductId = window.location.pathname.replace(/[^a-zA-Z0-9_-]/g, '_').slice(-30) || 'unknown_item';
            }
          }

          let price = null;
          let currency = 'INR';
          let inStock = true;

          // 1. Try structured JSON-LD (<script type="application/ld+json">)
          const scripts = document.querySelectorAll('script[type="application/ld+json"]');
          for (const s of scripts) {
            try {
              const parsed = JSON.parse(s.textContent || '');
              const items = Array.isArray(parsed) ? parsed : [parsed];
              for (const item of items) {
                if (item && (item['@type'] === 'Product' || item['@type'] === 'http://schema.org/Product')) {
                  const offer = Array.isArray(item.offers) ? item.offers[0] : item.offers;
                  if (offer) {
                    if (offer.price !== undefined) price = String(offer.price).replace(/[^0-9.]/g, '');
                    if (offer.priceCurrency) currency = String(offer.priceCurrency).toUpperCase();
                    if (offer.availability) {
                      inStock = String(offer.availability).toLowerCase().includes('instock');
                    }
                  }
                  break;
                }
              }
            } catch (_) {}
            if (price) break;
          }

          // 2. Fallback to OpenGraph / Product Meta Tags
          if (!price) {
            const priceMeta = document.querySelector('meta[property="product:price:amount"], meta[property="og:price:amount"]');
            if (priceMeta) price = priceMeta.getAttribute('content')?.replace(/[^0-9.]/g, '') || null;
            const currMeta = document.querySelector('meta[property="product:price:currency"], meta[property="og:price:currency"]');
            if (currMeta) currency = currMeta.getAttribute('content')?.toUpperCase() || 'INR';
          }

          // 3. Fallback to DOM elements for known shopping sites
          if (!price && retailer === 'amazon_in') {
            const amazonPriceElem = document.querySelector('.a-price .a-offscreen, #priceblock_ourprice, #priceblock_dealprice');
            if (amazonPriceElem && amazonPriceElem.textContent) {
              price = amazonPriceElem.textContent.replace(/[^0-9.]/g, '') || null;
            }
          }

          return {
            success: true,
            snapshot: {
              retailer,
              retailer_product_id: retailerProductId,
              title: title.slice(0, 500),
              url,
              price: price || '0.00',
              currency,
              availability: inStock ? 'in_stock' : 'out_of_stock',
              captured_at: new Date().toISOString()
            }
          };
        } catch (err) {
          return { success: false, error: String(err) };
        }
      })()
    `, true)

    return rawData
  } catch (error) {
    return { success: false, error: String(error) }
  }
})

app.whenReady().then(() => {
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
