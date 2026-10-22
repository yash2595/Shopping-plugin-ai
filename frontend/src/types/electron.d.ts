export interface ViewportBounds {
  x: number
  y: number
  width: number
  height: number
}

export interface NavigationStatePayload {
  tabId: string
  canGoBack: boolean
  canGoForward: boolean
}

export interface PageUrlPayload {
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

export interface ElectronAPI {
  navigate: (url: string, tabId?: string) => void
  goBack: (tabId?: string) => void
  goForward: (tabId?: string) => void
  reload: (tabId?: string) => void
  createTab: (tabId: string, url: string, activate?: boolean) => void
  switchTab: (tabId: string) => void
  closeTab: (tabId: string) => void
  updateViewportBounds: (bounds: ViewportBounds) => void
  onPageLoading: (callback: (payload: PageLoadingPayload) => void) => () => void
  onPageTitleChange: (callback: (payload: PageTitlePayload) => void) => () => void
  onPageUrlChange: (callback: (payload: PageUrlPayload) => void) => () => void
  onNavigationState: (callback: (payload: NavigationStatePayload) => void) => () => void
  onNewTabRequested: (callback: (payload: NewTabPayload) => void) => () => void
  updateOrbOverlay: (bounds: OrbOverlayBounds | null) => void
  activateOrb: () => void
  setOrbPointerInside: (isInside: boolean) => void
  setOrbSpeaking: (speaking: boolean) => void
  onOrbActivated: (callback: () => void) => () => void
  updateOrbOverlayState: (state: string, isPanelOpen: boolean) => void
  onOrbOverlayState: (callback: (state: string) => void) => () => void
  onOrbOverlayMode: (callback: (mode: 'corner' | 'focused') => void) => () => void
  onOrbOverlaySpeaking: (callback: (speaking: boolean) => void) => () => void
  demoFocusSearch: (tabId?: string) => Promise<void>
  demoType: (text: string, tabId?: string) => Promise<void>
  demoSubmitSearch: (tabId?: string) => Promise<void>
  demoCollectResults: (tabId?: string) => Promise<Array<{ href: string; text: string }>>
  demoOpenResult: (index: number, tabId?: string) => Promise<void>
  getAuthToken: () => Promise<string | null>
  setAuthToken: (token: string) => Promise<boolean>
  clearAuthToken: () => Promise<boolean>
  extractPageSnapshot: (tabId?: string) => Promise<{
    success: boolean
    snapshot?: {
      retailer: string
      retailer_product_id: string
      title: string
      url: string
      price: string
      currency: string
      availability: string
      captured_at: string
    }
    error?: string
  }>
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}
