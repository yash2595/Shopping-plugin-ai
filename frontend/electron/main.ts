import { app, BrowserWindow, WebContentsView, ipcMain, session } from 'electron'
import * as path from 'path'
import * as fs from 'fs'
import { extractStructuredOffers } from '../src/utils/offerExtractor.js'

// Keep Electron's runtime profile in the writable project workspace. The host
// profile can be access-denied in managed environments, which breaks Chromium
// cache/GPU startup before the renderer can paint the overlay.
if (!app.isPackaged) app.setPath('userData', path.join(process.cwd(), '.electron-runtime'))
app.disableHardwareAcceleration()
app.name = 'Copilot Browser'

let mainWindow: BrowserWindow | null = null
const views = new Map<string, WebContentsView>()
const documentVersions = new Map<string, number>()

function isTrustedUi(event: Electron.IpcMainInvokeEvent | Electron.IpcMainEvent) {
  return !!mainWindow && event.sender === mainWindow.webContents && event.senderFrame === mainWindow.webContents.mainFrame
}

function assertTrustedUi(event: Electron.IpcMainInvokeEvent) {
  if (!isTrustedUi(event)) {
    throw new Error('Untrusted application frame')
  }
}
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
if (!app.isPackaged && process.env.SAARTHI_REMOTE_DEBUGGING_PORT) {
  const debugPort = Number(process.env.SAARTHI_REMOTE_DEBUGGING_PORT)
  if (Number.isInteger(debugPort) && debugPort >= 1024 && debugPort <= 65535) {
    app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
    app.commandLine.appendSwitch('remote-debugging-port', String(debugPort))
  }
}

// On Windows (Intel Iris Xe / certain AMD/NVIDIA configs) the Chromium GPU
// process sandbox fails to initialise and exits immediately with
// exit_code=-1073741515 (0xC0000135 – STATUS_DLL_NOT_FOUND).  Disabling the
// GPU sandbox fixes the crash while keeping hardware-accelerated rendering.
// This is NOT the same as `--disable-gpu`; full GPU acceleration is retained.
if (!app.isPackaged && process.env.SHOPPING_UNSAFE_DEV_SANDBOX === '1') {
  app.commandLine.appendSwitch('disable-gpu-sandbox')
  app.commandLine.appendSwitch('no-sandbox')
}
// The workaround above is opt-in for development only; packaged releases retain sandboxing.

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
    console.log(`[WebContentsView:${tabId}] Intercepted new-tab request`)
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

  view.webContents.on('did-navigate', (_e, _navUrl) => {
    documentVersions.set(tabId, (documentVersions.get(tabId) || 0) + 1)
    console.log(`[WebContentsView:${tabId}] Page navigation completed`)
    sendNavState()
  })

  view.webContents.on('did-navigate-in-page', (_e, _navUrl) => {
    documentVersions.set(tabId, (documentVersions.get(tabId) || 0) + 1)
    console.log(`[WebContentsView:${tabId}] In-page navigation completed`)
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
  console.log(`[WebContentsView:${tabId}] Loading initial page`)
  view.webContents.loadURL(finalUrl)

  return view
}

function switchActiveTab(tabId: string) {
  if (tabId !== activeTabId) conversationNavigationCancels.get(activeTabId)?.()
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
  conversationNavigationCancels.get(tabId)?.()
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
  overlay.webContents.on('will-navigate', (event) => event.preventDefault())
  const markReady = (source: string) => {
    if (overlay.isDestroyed() || orbOverlayWindow !== overlay) return
    orbOverlayReady = true
    console.log(`[OrbOverlay] Ready via ${source}`)
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
      sandbox: true,
    },
  })

  mainWindow.webContents.on('will-navigate', (event) => event.preventDefault())
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      if (new URL(url).protocol === 'https:') mainWindow?.webContents.send('browser:new-tab-requested', { url })
    } catch { /* Invalid URLs are never opened. */ }
    return { action: 'deny' }
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
  if (!isTrustedUi(_event)) return
  const { tabId, url, activate = true } = payload
  if (!views.has(tabId)) {
    createTabView(tabId, url || 'https://www.google.com')
  }
  if (activate) {
    switchActiveTab(tabId)
  }
})

ipcMain.on('browser:switch-tab', (_event, payload: { tabId: string }) => {
  if (!isTrustedUi(_event)) return
  switchActiveTab(payload.tabId)
})

ipcMain.on('browser:close-tab', (_event, payload: { tabId: string }) => {
  if (!isTrustedUi(_event)) return
  closeTabView(payload.tabId)
})

ipcMain.on('browser:update-bounds', (_event, bounds) => {
  if (!isTrustedUi(_event)) return
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

const conversationNavigationCancels = new Map<string, () => void>()
// Conversation navigation accepts bounded commands from the trusted UI only.
function navigationContext() {
  const view = views.get(activeTabId)
  if (!view || view.webContents.isDestroyed()) throw new Error('No active browser tab')
  return {
    tabId: activeTabId, url: view.webContents.getURL(),
    target: { tab_id: view.webContents.id, document_id: `document-${view.webContents.id}-${documentVersions.get(activeTabId) || 0}`,
      active: true, window_focused: !!mainWindow?.isFocused() },
  }
}
ipcMain.handle('browser:navigation-context', (event) => { assertTrustedUi(event); return navigationContext() })
const navigationExecutions = new Map<string, { fingerprint: string; result: Promise<{ success: boolean; message: string }> }>()
async function executeConversationNavigation(event: Electron.IpcMainInvokeEvent, data: {
  kind: string; tabId: string; expectedUrl: string; url?: string; targetTabId?: string; index?: number; executionId?: string; expectedDocumentId?: string;
}) {
  assertTrustedUi(event)
  const view = views.get(data?.tabId)
  if (!view || data.tabId !== activeTabId || (!mainWindow?.isFocused() && data.kind !== 'stop') ||
      (data.kind !== 'stop' && view.webContents.getURL() !== data.expectedUrl)) return { success: false, message: 'The active page changed. Please repeat your request.' }
  const wc = view.webContents
  if (data.executionId && data.kind !== 'stop' && data.expectedDocumentId !== `document-${wc.id}-${documentVersions.get(data.tabId) || 0}`) return { success: false, message: 'The source document changed before execution.' }
  if (data.kind === 'stop') {
    documentVersions.set(data.tabId, (documentVersions.get(data.tabId) || 0) + 1)
    conversationNavigationCancels.get(data.tabId)?.()
    wc.stop()
    return { success: true, message: 'Navigation stopped.' }
  }
  if (data.kind === 'open_result') {
    if (!Number.isInteger(data.index) || data.index! < 1 || data.index! > 5) return { success: false, message: 'Choose a result number from 1 to 5.' }
    const source = new URL(wc.getURL())
    if (!['amazon.in', 'www.amazon.in', 'flipkart.com', 'www.flipkart.com'].includes(source.hostname) ||
        !['/s', '/search'].includes(source.pathname)) return { success: false, message: 'Open a supported Amazon or Flipkart search page first.' }
    const version = documentVersions.get(data.tabId) || 0
    try {
      const links = await wc.executeJavaScript(`(() => {
        const selector = location.hostname.endsWith('amazon.in') ? '[data-component-type="s-search-result"] h2 a[href], [data-component-type="s-search-result"] a[href] h2' : 'a[href*="/p/itm"]';
        const results = [];
        for (const node of [...document.querySelectorAll(selector)].slice(0, 100)) {
          const a = node.closest('a'); if (!a) continue;
          const rect = a.getBoundingClientRect(); const style = getComputedStyle(a);
          if (!rect.width || !rect.height || style.display === 'none' || style.visibility === 'hidden') continue;
          try {
            const u = new URL(a.href, location.href);
            if (u.protocol !== 'https:' || u.username || u.password || u.port || u.hostname !== location.hostname) continue;
            const asin = /\\/dp\\/([A-Z0-9]{10})(?:[/?]|$)/i.exec(u.pathname);
            const path = asin ? '/dp/' + asin[1] : /\\/p\\/itm[a-z0-9]+/i.test(u.pathname) ? u.pathname : null;
            if (!path) continue;
            const href = u.origin + path;
            if (!results.includes(href)) results.push(href);
            if (results.length === 5) break;
          } catch {}
        }
        return results;
      })()`)
      if (wc.getURL() !== source.href || activeTabId !== data.tabId || (documentVersions.get(data.tabId) || 0) !== version) return { success: false, message: 'Search results changed. Please repeat your request.' }
      if (!Array.isArray(links) || typeof links[data.index! - 1] !== 'string') return { success: false, message: 'That product result is unavailable. Check the search page or scroll to load results.' }
      // Reuse URL validation and actual-load receipt; never click page-provided controls.
      const chosen = new URL(links[data.index! - 1])
      if (chosen.origin !== source.origin || !/\/dp\/[A-Z0-9]{10}$|\/p\/itm[a-z0-9]+/i.test(chosen.pathname)) throw new Error()
      data = { ...data, kind: 'navigate', url: chosen.href }
    } catch { return { success: false, message: 'Product result navigation is unavailable.' } }
  }
  if (data.kind === 'scroll_down' || data.kind === 'scroll_up') {
    if (!/^https:\/\//i.test(wc.getURL())) return { success: false, message: 'Scrolling requires a web page.' }
    const originalUrl = wc.getURL()
    try {
      // Fixed packaged code: no selectors, page text or model-authored JavaScript.
      const delta = data.kind === 'scroll_down' ? 600 : -600
      const moved = await wc.executeJavaScript(`(() => { const before = window.scrollY; window.scrollBy({ top: ${delta}, behavior: 'instant' }); return window.scrollY !== before })()`)
      if (activeTabId !== data.tabId || wc.getURL() !== originalUrl) return { success: false, message: 'The active page changed.' }
      return { success: moved === true, message: moved === true ? 'Page scrolled.' : 'The page did not scroll; it may already be at its edge.' }
    } catch { return { success: false, message: 'Scrolling is unavailable on this page.' } }
  }
  if (data.kind === 'switch') {
    if (!data.targetTabId || !views.has(data.targetTabId)) return { success: false, message: 'That tab is no longer open.' }
    switchActiveTab(data.targetTabId)
    return { success: activeTabId === data.targetTabId, message: 'Switched to the requested tab.' }
  }
  if (!['navigate', 'back', 'forward', 'reload'].includes(data.kind)) return { success: false, message: 'Unsupported browser command.' }
  let destination: URL | undefined
  if (data.kind === 'navigate') {
    try {
      if (!data.url || data.url.length > 2000 || (/[\s\\]/u.test(data.url) || Array.from(data.url).some((char) => char.charCodeAt(0) < 32))) throw new Error()
      destination = new URL(data.url)
      if (destination.protocol !== 'https:' || destination.username || destination.password || destination.port ||
          !destination.hostname.includes('.') || /^(localhost|127\.|0\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/i.test(destination.hostname) ||
          /\.(localhost|local|internal)$/i.test(destination.hostname) ||
          /\/(?:cart|checkout|payment|buy|order|logout|delete)(?:[/?-]|$)/i.test(destination.pathname)) throw new Error()
    } catch { return { success: false, message: 'This destination is not allowed for assistant navigation.' } }
  }
  const history = wc.navigationHistory
  if (data.kind === 'back' && !history.canGoBack() || data.kind === 'forward' && !history.canGoForward()) return { success: false, message: 'No page is available in that direction.' }
  conversationNavigationCancels.get(data.tabId)?.()
  return await new Promise<{ success: boolean; message: string }>((resolve) => {
    let settled = false
    const finish = (success: boolean, message: string) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      conversationNavigationCancels.delete(data.tabId)
      wc.removeListener('did-finish-load', loaded)
      wc.removeListener('did-fail-load', failed)
      resolve({ success, message })
    }
    const loaded = () => finish(true, 'Page loaded. What would you like to do here?')
    const failed = (_event: unknown, _code: number, _description: string, _url: string, isMainFrame: boolean) => { if (isMainFrame) finish(false, 'The page did not load. Please retry or choose another website.') }
    const timer = setTimeout(() => { finish(false, 'Navigation timed out. Please check the page before retrying.'); wc.stop() }, 30000)
    conversationNavigationCancels.set(data.tabId, () => finish(false, 'Navigation stopped.'))
    wc.on('did-finish-load', loaded)
    wc.on('did-fail-load', failed)
    if (destination) void wc.loadURL(destination.href).catch(() => finish(false, 'The page did not load.'))
    else if (data.kind === 'back') history.goBack()
    else if (data.kind === 'forward') history.goForward()
    else wc.reload()
  })
}

ipcMain.handle('browser:conversation-navigation', async (event, data: Parameters<typeof executeConversationNavigation>[1]) => {
  assertTrustedUi(event)
  if (!data?.executionId) return executeConversationNavigation(event, data)
  if (!/^[a-zA-Z0-9_.:-]{1,100}$/.test(data.executionId)) return { success: false, message: 'Invalid navigation step identity.' }
  const fingerprint = JSON.stringify(data)
  const existing = navigationExecutions.get(data.executionId)
  if (existing) return existing.fingerprint === fingerprint ? existing.result : { success: false, message: 'Navigation step identity was already used for another action.' }
  if (navigationExecutions.size >= 500) return { success: false, message: 'Navigation journal is full. Restart the app before starting another plan.' }
  const result = executeConversationNavigation(event, data)
  navigationExecutions.set(data.executionId, { fingerprint, result })
  return result
})

ipcMain.on('browser:navigate', (_event, payload: { url: string; tabId?: string } | string) => {
  if (!isTrustedUi(_event)) return
  const rawUrl = typeof payload === 'string' ? payload : payload.url
  const targetId = (typeof payload === 'object' && payload.tabId) || activeTabId
  const targetView = views.get(targetId)
  if (!targetView) return

  conversationNavigationCancels.get(targetId)?.()
  const finalUrl = normalizeUrl(rawUrl)
  console.log(`[WebContentsView:${targetId}] Navigating to requested page`)
  conversationNavigationCancels.get(targetId)?.()
  targetView.webContents.loadURL(finalUrl)
})

ipcMain.on('browser:go-back', (_event, payload?: { tabId?: string }) => {
  if (!isTrustedUi(_event)) return
  const targetId = payload?.tabId || activeTabId
  const targetView = views.get(targetId)
  if (!targetView) return

  const nav = targetView.webContents.navigationHistory
  if (nav && nav.canGoBack()) {
    conversationNavigationCancels.get(targetId)?.()
    nav.goBack()
  }
})

ipcMain.on('browser:go-forward', (_event, payload?: { tabId?: string }) => {
  if (!isTrustedUi(_event)) return
  const targetId = payload?.tabId || activeTabId
  const targetView = views.get(targetId)
  if (!targetView) return

  const nav = targetView.webContents.navigationHistory
  if (nav && nav.canGoForward()) {
    conversationNavigationCancels.get(targetId)?.()
    nav.goForward()
  }
})

ipcMain.on('browser:reload', (_event, payload?: { tabId?: string }) => {
  if (!isTrustedUi(_event)) return
  const targetId = payload?.tabId || activeTabId
  const targetView = views.get(targetId)
  if (targetView) {
    conversationNavigationCancels.get(targetId)?.()
    targetView.webContents.reload()
  }
})

ipcMain.handle('browser:demo-focus-search', (_event, tabId?: string) => { assertTrustedUi(_event); return focusBrowserSearch(tabId) })
ipcMain.handle('browser:demo-type', async (_event, text: string, tabId?: string) => {
  assertTrustedUi(_event)
  try {
    return await typeBrowserText(text, tabId)
  } catch (error) {
    console.error('[IPC browser:demo-type] failed', error)
    throw error
  }
})
ipcMain.handle('browser:demo-submit-search', (_event, tabId?: string) => { assertTrustedUi(_event); return submitBrowserSearch(tabId) })
ipcMain.handle('browser:demo-collect-results', (_event, tabId?: string) => { assertTrustedUi(_event); return collectBrowserResults(tabId) })
ipcMain.handle('browser:demo-open-result', (_event, index: number, tabId?: string) => { assertTrustedUi(_event); return openBrowserResult(index, tabId) })

// Secure In-Memory Auth Vault (Keeps tokens safe in main process without leaking to disk/localStorage)
let secureAuthToken: string | null = null

ipcMain.handle('auth:get-token', (event) => { assertTrustedUi(event); return secureAuthToken })
ipcMain.handle('auth:set-token', (_event, token: string) => {
  assertTrustedUi(_event)
  secureAuthToken = typeof token === 'string' && token.trim().length > 0 && token.length <= 8192 && !/[\r\n]/.test(token) ? token.trim() : null
  return true
})
ipcMain.handle('auth:clear-token', (event) => {
  assertTrustedUi(event)
  secureAuthToken = null
  return true
})

// Packaged file:// renderers use a trusted native HTTP bridge instead of widening
// CORS to null origins. Retailer cookies and arbitrary destinations are excluded.
ipcMain.handle('backend:request', async (event, data: {
  url: string; method: string; headers: Record<string, string>; body?: string;
}) => {
  assertTrustedUi(event)
  try {
    const base = new URL(process.env.SHOPPING_BACKEND_URL || 'http://localhost:8000')
    const requested = new URL(data.url)
    const target = new URL(requested.pathname + requested.search, base.origin)
    const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)
    if (base.protocol !== 'https:' && !(base.protocol === 'http:' && loopback)
        || base.username || base.password || base.search || base.hash || requested.username || requested.password
        || ![base.origin, 'http://localhost:8000'].includes(requested.origin) || !target.pathname.startsWith('/v1/')
        || !['GET', 'POST', 'PATCH', 'DELETE'].includes(data.method)
        || data.body !== undefined && (typeof data.body !== 'string' || Buffer.byteLength(data.body) > 262144)) {
      throw new Error('Invalid backend request')
    }
    const headers: Record<string, string> = { Accept: 'application/json' }
    for (const [key, value] of Object.entries(data.headers || {})) {
      const name = key.toLowerCase()
      if (['content-type', 'x-event-id', 'x-expected-version'].includes(name) && typeof value === 'string') headers[name] = value
      if (name === 'authorization' && secureAuthToken) headers.Authorization = `Bearer ${secureAuthToken}`
    }
    const response = await fetch(target, { method: data.method, headers, body: data.body,
      redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(120000) })
    const reader = response.body?.getReader()
    const chunks: Uint8Array[] = []
    let size = 0
    if (reader) {
      try {
        while (true) {
          const part = await reader.read()
          if (part.done) break
          size += part.value.byteLength
          if (size > 1048576) throw new Error('Response too large')
          chunks.push(part.value)
        }
      } finally { await reader.cancel().catch(() => undefined) }
    }
    const allowedHeaders: Record<string, string> = {}
    for (const name of ['content-type', 'idempotent-replay', 'x-conversation-version', 'x-request-id']) {
      const value = response.headers.get(name)
      if (value) allowedHeaders[name] = value
    }
    return { status: response.status, headers: allowedHeaders, body: Buffer.concat(chunks).toString('utf8') }
  } catch {
    return { status: 503, headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ error: { code: 'BACKEND_UNAVAILABLE', message: 'The shopping service is unavailable. Check its local setup and retry.' } }) }
  }
})

ipcMain.handle('browser:extract-offers', async (event, tabId: string) => {
  assertTrustedUi(event)
  const view = views.get(tabId)
  if (tabId !== activeTabId || !mainWindow?.isFocused() || !view || view.webContents.isDestroyed()) return { success: false }
  const wc = view.webContents, address = wc.getURL(), version = documentVersions.get(tabId) || 0
  try {
    const source = new URL(address)
    if (source.protocol !== 'https:' || source.username || source.password || source.port ||
        !['www.amazon.in', 'amazon.in', 'www.flipkart.com', 'flipkart.com'].includes(source.hostname) ||
        !(source.hostname.endsWith('amazon.in') ? source.pathname === '/s' : source.pathname === '/search')) return { success: false }
    const offers = await wc.executeJavaScript(`(${extractStructuredOffers.toString()})(document, window.location.href)`)
    if (wc.isDestroyed() || wc.getURL() !== address || (documentVersions.get(tabId) || 0) !== version || activeTabId !== tabId || !mainWindow?.isFocused()) return { success: false }
    if (!Array.isArray(offers) || offers.length > 20 || JSON.stringify(offers).length > 100000) return { success: false }
    const observed_at = new Date().toISOString()
    return { success: true, document_id: `document-${wc.id}-${version}`, offers: offers.map((offer) => ({ ...offer,
      offer_id: crypto.randomUUID(), snapshot_id: crypto.randomUUID(), observed_at })) }
  } catch { return { success: false } }
})

// Safe Snapshot Extraction from Active Chromium WebContentsView
ipcMain.handle('browser:extract-snapshot', async (_event, tabId?: string) => {
  assertTrustedUi(_event)
  const targetId = tabId || activeTabId
  const targetView = views.get(targetId)
  if (!targetView || targetView.webContents.isDestroyed()) {
    return { success: false, error: 'No active web view available' }
  }

  const sourceUrl = targetView.webContents.getURL()
  const parsedSource = new URL(sourceUrl)
  if (parsedSource.protocol !== 'https:' || parsedSource.username || parsedSource.password || !['www.amazon.in', 'amazon.in', 'www.flipkart.com', 'flipkart.com'].includes(parsedSource.hostname)) {
    return { success: false, error: 'Open a supported Amazon.in or Flipkart product page' }
  }
  if (!/\/(?:dp|gp\/product|ASIN)\/[a-z0-9]{10}(?:\/|$)/i.test(parsedSource.pathname)
      && !/\/p\/itm[a-z0-9]+(?:\/|$)/i.test(parsedSource.pathname)) {
    return { success: false, error: 'Open an identified product page, not a cart, account or checkout page' }
  }
  const version = documentVersions.get(targetId) || 0
  const documentId = `document-${targetView.webContents.id}-${version}`
  try {
    const rawData = await targetView.webContents.executeJavaScript(`
      (() => {
        try {
          const clip = (text, limit) => Array.from(text).slice(0, limit).join('');
          const parsePrice = (value) => {
            const text = String(value).trim();
            const match = text.match(/^(?:₹|INR|Rs\\.?)?\\s*((?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})*,[0-9]{3})(?:\\.[0-9]{1,2})?)$/i);
            return match ? match[1].replace(/,/g, '') : null;
          };
          const pageSource = new URL(window.location.href);
          const url = pageSource.origin + pageSource.pathname;
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
          let inStock = null;

          // 1. Try structured JSON-LD (<script type="application/ld+json">)
          const scripts = document.querySelectorAll('script[type="application/ld+json"]');
          for (const s of Array.from(scripts).slice(0, 20)) {
            try {
              const source = s.textContent || '';
              if (source.length > 100000) continue;
              const parsed = JSON.parse(source);
              const items = Array.isArray(parsed) ? parsed : [parsed];
              for (const item of items) {
                if (item && (item['@type'] === 'Product' || item['@type'] === 'http://schema.org/Product')) {
                  const offer = Array.isArray(item.offers) ? (item.offers.length === 1 ? item.offers[0] : null) : item.offers;
                  if (offer) {
                    if (offer.price !== undefined) price = parsePrice(offer.price);
                    if (offer.priceCurrency) currency = String(offer.priceCurrency).toUpperCase();
                    if (offer.availability) {
                      const availability = String(offer.availability).toLowerCase();
                      inStock = (availability === 'instock' || availability.endsWith('/instock')) ? true : (availability === 'outofstock' || availability.endsWith('/outofstock')) ? false : null;
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
            if (priceMeta) price = parsePrice(priceMeta.getAttribute('content') || '');
            const currMeta = document.querySelector('meta[property="product:price:currency"], meta[property="og:price:currency"]');
            if (currMeta) currency = currMeta.getAttribute('content')?.toUpperCase() || 'INR';
          }

          // 3. Fallback to DOM elements for known shopping sites
          if (!price && retailer === 'amazon_in') {
            const amazonPriceElem = document.querySelector('#corePriceDisplay_desktop_feature_div .a-offscreen, #corePrice_feature_div .a-offscreen, #priceblock_ourprice, #priceblock_dealprice');
            if (amazonPriceElem && amazonPriceElem.textContent) {
              price = parsePrice(amazonPriceElem.textContent);
            }
          }

          const visiblePriceNode = document.querySelector('#corePriceDisplay_desktop_feature_div .a-offscreen, #corePrice_feature_div .a-offscreen, #priceblock_ourprice, #priceblock_dealprice');
          const visibleText = visiblePriceNode?.textContent || '';
          const visiblePrice = parsePrice(visibleText);
          if (visiblePrice && price && (Number(visiblePrice) !== Number(price) || (visibleText.includes('₹') && currency !== 'INR'))) {
            price = null; currency = null;
          }
          if (!asinMatch && !url.match(/\\/p\\/(itm[a-zA-Z0-9]+)/i)) {
            return { success: false, error: 'This is not an identified product page' };
          }
          const specs = {};
          for (const row of Array.from(document.querySelectorAll('#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, #technicalSpecifications_section_1 tr, ._14cfVK tr, ._3k-BhJ tr, table.specification tr')).slice(0, 100)) {
            const cells = row.querySelectorAll('th, td');
            if (cells.length < 2) continue;
            const key = clip((cells[0].innerText || cells[0].textContent || '').trim(), 80);
            const value = clip((cells[1].innerText || cells[1].textContent || '').trim(), 500);
            if (key && value && Object.keys(specs).length < 30 && !['__proto__','constructor','prototype'].includes(key)) specs[key] = value;
          }
          const reviews = [];
          let reviewChars = 0;
          for (const node of Array.from(document.querySelectorAll('[data-hook="review"], ._27M-vq')).slice(0, 30)) {
            const body = node.querySelector('[data-hook="review-body"], .ZmyHeo');
            const stars = node.querySelector('[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"], .XQDdHH');
            const text = clip((body?.innerText || body?.textContent || '').trim(), 1000);
            const rating = Number((stars?.textContent || '').trim().match(/^[1-5](?:\\.0)?(?![0-9.])/)?.[0]);
            if (!text || !Number.isInteger(rating) || rating < 1 || rating > 5) continue;
            if (reviewChars + text.length > 15000) break;
            reviewChars += text.length;
            reviews.push({ review_id: 'review-' + reviews.length, rating, text,
              verified_purchase_observed: /verified purchase/i.test(node.querySelector('[data-hook="avp-badge"]')?.textContent || '') ? true : null });
          }
          return {
            success: true,
            snapshot: {
              retailer,
              retailer_product_id: retailerProductId,
              title: clip(title, 500),
              url,
              price: price || '0.00',
              currency,
              availability: inStock === true ? 'in_stock' : inStock === false ? 'out_of_stock' : 'unknown',
              specs, reviews,
              captured_at: new Date().toISOString()
            }
          };
        } catch (err) {
          return { success: false, error: String(err) };
        }
      })()
    `, true)

    if (targetView.webContents.isDestroyed() || targetView.webContents.getURL() !== sourceUrl || (documentVersions.get(targetId) || 0) !== version) {
      return { success: false, error: 'The page changed while reading; capture it again' }
    }
    if (rawData?.snapshot) rawData.snapshot.document_id = documentId
    return rawData
  } catch (error) {
    return { success: false, error: String(error) }
  }
})

ipcMain.handle('browser:analysis-skill', async (event, data: {
  skill: string; document_id: string; source_url: string; spec_key?: string | null;
  review_id?: string | null; expected_text: string;
}) => {
  assertTrustedUi(event)
  if (!data || !['highlight_spec', 'scroll_to_reviews'].includes(data.skill)
      || typeof data.expected_text !== 'string' || !data.expected_text || Array.from(data.expected_text).length > 500
      || typeof data.spec_key === 'string' && Array.from(data.spec_key).length > 80
      || typeof data.review_id === 'string' && !/^review-\d{1,2}$/.test(data.review_id)) {
    return { success: false, error: 'Unsupported page action' }
  }
  const view = views.get(activeTabId)
  if (!view || view.webContents.isDestroyed() || !mainWindow?.isFocused()) return { success: false, error: 'Bring the product tab to the foreground' }
  const expectedDocument = `document-${view.webContents.id}-${documentVersions.get(activeTabId) || 0}`
  const currentUrl = view.webContents.getURL()
  let source: URL
  try { source = new URL(currentUrl) } catch { return { success: false, error: 'No product page' } }
  if (expectedDocument !== data.document_id || source.origin + source.pathname !== data.source_url || source.protocol !== 'https:'
      || !['amazon.in', 'www.amazon.in', 'flipkart.com', 'www.flipkart.com'].includes(source.hostname)) {
    return { success: false, error: 'Page changed. Capture fresh evidence before continuing.' }
  }
  const args = { skill: data.skill, spec_key: data.spec_key, review_id: data.review_id, expected_text: data.expected_text }
  try {
    return await view.webContents.executeJavaScript(`(() => {
      const args = ${JSON.stringify(args)};
      const clip = (text, limit) => Array.from(text).slice(0, limit).join('');
      let target = null;
      if (args.skill === 'highlight_spec') {
        for (const row of Array.from(document.querySelectorAll('#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, #technicalSpecifications_section_1 tr, ._14cfVK tr, ._3k-BhJ tr, table.specification tr')).slice(0, 100)) {
          const cells = row.querySelectorAll('th, td');
          if (cells.length < 2) continue;
          const key = clip((cells[0].innerText || cells[0].textContent || '').trim(), 80);
          const value = clip((cells[1].innerText || cells[1].textContent || '').trim(), 500);
          if (key === args.spec_key && value === args.expected_text) { target = row; break; }
        }
      } else {
        let index = 0;
        for (const node of Array.from(document.querySelectorAll('[data-hook="review"], ._27M-vq')).slice(0, 30)) {
          const body = node.querySelector('[data-hook="review-body"], .ZmyHeo');
          const stars = node.querySelector('[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"], .XQDdHH');
          const text = clip((body?.innerText || body?.textContent || '').trim(), 1000);
          const rating = Number((stars?.textContent || '').trim().match(/^[1-5](?:\\.0)?(?![0-9.])/)?.[0]);
          if (!text || !Number.isInteger(rating) || rating < 1 || rating > 5) continue;
          if ('review-' + index === args.review_id && text.includes(args.expected_text)) { target = node; break; }
          index++;
        }
      }
      if (!target || !target.getClientRects().length) return { success: false, error: 'Evidence no longer matches the visible page' };
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      const previous = target.style.outline;
      target.style.outline = '3px solid #22d3ee';
      setTimeout(() => { target.style.outline = previous; }, 2500);
      return { success: true };
    })()`, true)
  } catch { return { success: false, error: 'Page highlighting is unavailable; the text evidence is still shown' } }
})

app.whenReady().then(() => {
  const permittedMedia = (contents: Electron.WebContents | null, permission: string) =>
    permission === 'media' && !!mainWindow && mainWindow.isFocused() && contents === mainWindow.webContents
  session.defaultSession.setPermissionCheckHandler((contents, permission) => permittedMedia(contents, permission))
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback) => callback(permittedMedia(contents, permission)))
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
