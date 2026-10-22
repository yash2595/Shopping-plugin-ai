/**
 * Utility to normalize URLs, search queries, and natural commands into valid web URLs.
 */

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
  '.me',
]

export function resolveNavigationUrl(input: string): string {
  let text = (input || '').trim()
  if (!text) return 'https://www.google.com'

  // Strip natural language navigation prefixes
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

  // Check if input directly starts with http:// or https://
  if (text.startsWith('http://') || text.startsWith('https://')) {
    return text
  }

  const cleanLower = text.toLowerCase().trim()

  // Check known aliases (e.g. "youtube", "wikipedia")
  if (KNOWN_DOMAINS[cleanLower]) {
    return KNOWN_DOMAINS[cleanLower]
  }

  // Check if it's a domain with a common TLD (e.g. "youtube.com", "news.ycombinator.com")
  const hasKnownTld = COMMON_TLDS.some((tld) => cleanLower.includes(tld))
  if (hasKnownTld && !cleanLower.includes(' ')) {
    return `https://${text}`
  }

  // Check generic domain pattern (foo.bar without spaces)
  if (/^[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(\/.*)?$/.test(cleanLower)) {
    return `https://${text}`
  }

  // Otherwise, treat as a search query on Google
  return `https://www.google.com/search?q=${encodeURIComponent(text)}`
}
