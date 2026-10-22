import type { BrowserTab } from '../types/browser'

export const INITIAL_TABS: BrowserTab[] = [
  {
    id: 'tab-1',
    title: 'Google',
    url: 'https://www.google.com',
    isActive: true,
  },
  {
    id: 'tab-2',
    title: 'arXiv: LLM Browser Agents',
    url: 'https://arxiv.org/abs/2402.04561',
    isActive: false,
  },
  {
    id: 'tab-3',
    title: 'GitHub — browser-use',
    url: 'https://github.com/browser-use/browser-use',
    isActive: false,
  },
]
