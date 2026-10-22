import { contextBridge, ipcRenderer } from 'electron'

export interface ViewportBounds {
  x: number
  y: number
  width: number
  height: number
}

export interface NavigationState {
  tabId?: string
  canGoBack: boolean
  canGoForward: boolean
}

export interface PageStatePayload {
  tabId: string
  url: string
}

export interface PageTitlePayload {
  tabId: string
  title: string
}

export interface PageLoadingPayload {
  tabId: string
  isLoading: boolean
}

export interface NewTabPayload {
  url: string
}

export interface OrbOverlayBounds {
  x: number
  y: number
  width: number
  height: number
}

const electronAPI = {
  navigate: (url: string, tabId?: string) => {
    ipcRenderer.send('browser:navigate', { url, tabId })
  },
  goBack: (tabId?: string) => {
    ipcRenderer.send('browser:go-back', { tabId })
  },
  goForward: (tabId?: string) => {
    ipcRenderer.send('browser:go-forward', { tabId })
  },
  reload: (tabId?: string) => {
    ipcRenderer.send('browser:reload', { tabId })
  },
  createTab: (tabId: string, url: string, activate = true) => {
    ipcRenderer.send('browser:create-tab', { tabId, url, activate })
  },
  switchTab: (tabId: string) => {
    ipcRenderer.send('browser:switch-tab', { tabId })
  },
  closeTab: (tabId: string) => {
    ipcRenderer.send('browser:close-tab', { tabId })
  },
  updateViewportBounds: (bounds: ViewportBounds) => {
    ipcRenderer.send('browser:update-bounds', bounds)
  },
  onPageLoading: (callback: (payload: { tabId: string; isLoading: boolean }) => void) => {
    const handler = (_event: any, payload: { tabId: string; isLoading: boolean }) => callback(payload)
    ipcRenderer.on('browser:loading', handler)
    return () => {
      ipcRenderer.removeListener('browser:loading', handler)
    }
  },
  onPageTitleChange: (callback: (payload: { tabId: string; title: string }) => void) => {
    const handler = (_event: any, payload: { tabId: string; title: string }) => callback(payload)
    ipcRenderer.on('browser:title-change', handler)
    return () => {
      ipcRenderer.removeListener('browser:title-change', handler)
    }
  },
  onPageUrlChange: (callback: (payload: { tabId: string; url: string }) => void) => {
    const handler = (_event: any, payload: { tabId: string; url: string }) => callback(payload)
    ipcRenderer.on('browser:url-change', handler)
    return () => {
      ipcRenderer.removeListener('browser:url-change', handler)
    }
  },
  onNavigationState: (callback: (payload: { tabId: string; canGoBack: boolean; canGoForward: boolean }) => void) => {
    const handler = (_event: any, payload: { tabId: string; canGoBack: boolean; canGoForward: boolean }) => callback(payload)
    ipcRenderer.on('browser:nav-state', handler)
    return () => {
      ipcRenderer.removeListener('browser:nav-state', handler)
    }
  },
  onNewTabRequested: (callback: (payload: { url: string }) => void) => {
    const handler = (_event: any, payload: { url: string }) => callback(payload)
    ipcRenderer.on('browser:new-tab-requested', handler)
    return () => {
      ipcRenderer.removeListener('browser:new-tab-requested', handler)
    }
  },
  updateOrbOverlay: (bounds: OrbOverlayBounds | null) => {
    ipcRenderer.send('orb:update-overlay', bounds)
  },
  activateOrb: () => {
    ipcRenderer.send('orb:activate')
  },
  setOrbPointerInside: (isInside: boolean) => {
    ipcRenderer.send('orb:pointer-inside', isInside)
  },
  setOrbSpeaking: (speaking: boolean) => {
    ipcRenderer.send('orb:speaking', speaking)
  },
  onOrbActivated: (callback: () => void) => {
    const handler = () => callback()
    ipcRenderer.on('orb:activated', handler)
    return () => {
      ipcRenderer.removeListener('orb:activated', handler)
    }
  },
  updateOrbOverlayState: (state: string, isPanelOpen: boolean) => {
    ipcRenderer.send('orb:update-state', { state, isPanelOpen })
  },
  onOrbOverlayState: (callback: (state: string) => void) => {
    const handler = (_event: any, state: string) => callback(state)
    ipcRenderer.on('orb:overlay-state', handler)
    return () => ipcRenderer.removeListener('orb:overlay-state', handler)
  },
  onOrbOverlayMode: (callback: (mode: 'corner' | 'focused') => void) => {
    const handler = (_event: any, mode: 'corner' | 'focused') => callback(mode)
    ipcRenderer.on('orb:overlay-mode', handler)
    return () => ipcRenderer.removeListener('orb:overlay-mode', handler)
  },
  onOrbOverlaySpeaking: (callback: (speaking: boolean) => void) => {
    const handler = (_event: any, speaking: boolean) => callback(Boolean(speaking))
    ipcRenderer.on('orb:overlay-speaking', handler)
    return () => ipcRenderer.removeListener('orb:overlay-speaking', handler)
  },
  demoFocusSearch: (tabId?: string) => ipcRenderer.invoke('browser:demo-focus-search', tabId),
  demoType: (text: string, tabId?: string) => ipcRenderer.invoke('browser:demo-type', text, tabId),
  demoSubmitSearch: (tabId?: string) => ipcRenderer.invoke('browser:demo-submit-search', tabId),
  demoCollectResults: (tabId?: string) => ipcRenderer.invoke('browser:demo-collect-results', tabId),
  demoOpenResult: (index: number, tabId?: string) => ipcRenderer.invoke('browser:demo-open-result', index, tabId),
  getAuthToken: () => ipcRenderer.invoke('auth:get-token'),
  setAuthToken: (token: string) => ipcRenderer.invoke('auth:set-token', token),
  clearAuthToken: () => ipcRenderer.invoke('auth:clear-token'),
  extractPageSnapshot: (tabId?: string) => ipcRenderer.invoke('browser:extract-snapshot', tabId),
}

contextBridge.exposeInMainWorld('electronAPI', electronAPI)
