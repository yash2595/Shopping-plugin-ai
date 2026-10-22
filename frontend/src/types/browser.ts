export interface BrowserTab {
  id: string
  title: string
  url: string
  favicon?: string
  isActive: boolean
  isLoading?: boolean
  canGoBack?: boolean
  canGoForward?: boolean
}

export interface ChatMessageItem {
  id: string
  sender: 'user' | 'assistant'
  text: string
  timestamp: string
}
