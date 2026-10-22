# Architecture Plan: Real Web Content Integration for SAARTHI Browser

**Document Version:** 1.0  
**Project:** SAARTHI (`F:\SAARTHI`)  
**Scope:** Frontend Architecture & Real Web Browser Integration  
**Target Audience:** Frontend Team, AI/Agent Engineers, Backend/Playwright Team  

---

## Executive Summary

The current SAARTHI frontend successfully delivers an approved, high-fidelity browser shell with an integrated AI co-pilot sidecar and prominent voice controls. However, the central browser viewport is currently a simulated static React component.

To transition from a simulated webpage to **real web content** (e.g., loading `google.com`, searching flights, navigating to live websites) while keeping the **SAARTHI UI completely unchanged**, we must overcome fundamental web security boundaries (`X-Frame-Options`, Content Security Policy, Same-Origin Policy).

This document evaluates the architectural pathways, outlines how our existing React components fit into the production system, defines the backend contract for the AI/Playwright team, and provides a clear technical recommendation.

---

## 1. How a React/Vite Frontend Can Display Real Web Pages

There are only **three technically viable mechanisms** to display arbitrary, real third-party websites alongside a custom React UI:

| Mechanism | Description | Real Website Compatibility | Interaction Latency |
| :--- | :--- | :--- | :--- |
| **A. Native Desktop WebContentsView (Electron / Native Shell)** | The frontend runs inside a native desktop shell. The central viewport is a native Chromium `WebContentsView` embedded into the window coordinates of the React layout. | **100%** (Identical to Google Chrome) | **0 ms** (Runs directly on local GPU/Chromium) |
| **B. Low-Latency Screencast Stream (CDP / WebRTC)** | A real Chromium browser runs on the backend (managed by Playwright). The backend captures frames via Chrome DevTools Protocol (`Page.startScreencast`) or WebRTC and streams them to an interactive `<canvas>` in the React viewport. Mouse and keyboard events are sent back over WebSocket. | **100%** (Real Playwright Chromium) | **10–40 ms** (Localhost), **50–120 ms** (Remote cloud) |
| **C. Standard Browser `<iframe>` with Reverse Proxy** | An `<iframe>` inside normal React, stripped of security headers via a local CORS/header-stripping proxy. | **< 30%** (Breaks on Google, logins, Google reCAPTCHA, Cloudflare, payment gateways, complex SPAs) | **0 ms** (Local render), but heavily broken |

---

## 2. Can a Normal React Webpage Safely Provide This Functionality?

> [!CAUTION]
> **No.** A standard React web application running in a regular browser (e.g., accessed at `http://localhost:7319` in Chrome) **cannot** safely or reliably embed real external websites using a standard `<iframe>`.

### Technical Reasons:

1. **`X-Frame-Options: SAMEORIGIN` and `DENY`**:
   Search engines (`google.com`), airlines, e-commerce sites, banks, and social platforms explicitly forbid being embedded in frames. Browsers refuse to render them, showing a gray screen: *"google.com refused to connect"*.
2. **Content Security Policy (`frame-ancestors 'none'` / `'self'`):**
   Modern websites specify CSP directives that instruct browsers to terminate any iframe embedding that does not originate from their own domain.
3. **Same-Origin Policy (SOP) & DOM Access Isolation:**
   Even if an iframe renders a simple permissive site, the parent React app cannot access or query the iframe's DOM (`SecurityError: Blocked a frame with origin "http://localhost:7319" from accessing a cross-origin frame`). The AI agent could not inspect elements through the web page without backend tooling.
4. **Third-Party Cookie Partitioning (CHIPS) & Authentication:**
   Modern browsers block third-party cookies inside iframes. Users cannot sign in to Google, Amazon, or airline sites inside an iframe.
5. **Why Header-Stripping Proxies Fail in Practice:**
   While a developer proxy can theoretically remove `X-Frame-Options` and rewrite CSP, modern web applications employ client-side framebusting JavaScript (`if (top !== self) top.location = self.location`), complex CORS checks for internal API calls, and anti-bot verification (Cloudflare Turnstile, reCAPTCHA) that immediately detect and block proxy framing.

**Conclusion:** A plain React webpage with an `<iframe>` is **not viable** for a serious browser agent product.

---

## 3. Evaluation of Shell Architectures

To display real web pages and enable Playwright automation, we evaluated four candidate architectures:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SAARTHI Architectural Pathways                        │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
┌──────────────────┐          ┌──────────────────┐          ┌──────────────────┐
│   Path 1:        │          │   Path 2:        │          │   Path 3:        │
│   Electron       │          │   Web-Based      │          │   Chrome         │
│   Desktop Shell  │          │   CDP Stream     │          │   Extension      │
│   (WebContents)  │          │   (Anthropic/    │          │   (Side Panel)   │
│                  │          │    Browserbase)  │          │                  │
└──────────────────┘          └──────────────────┘          └──────────────────┘
```

### Option 1: Electron Desktop Application Shell (Desktop Target)
- **How it works:** Wrap our existing Vite/React app in an Electron shell. The main window renders the top toolbar, left sidebar, and Saarthi panel. The central viewport area is reserved for a native `WebContentsView` (the modern Electron API replacing `BrowserView` and `<webview>`).
- **Playwright Integration:** Native and first-class. Playwright connects directly to Electron's Chromium instance via Chrome DevTools Protocol (`playwright.chromium.connectOverCDP('http://localhost:9222')`) or `_electron.launch()`.
- **Security Bypass:** Electron has full control over request headers and web security flags (`webPreferences: { webSecurity: true, contextIsolation: true }`). It navigates to real Google, airline portals, etc., just like Google Chrome.
- **Saarthi UI Impact:** Zero redesign. The exact same React components render the UI chrome.

### Option 2: Web-Based Live Viewport Streaming (Anthropic browser-use / Hyperbrowser Pattern)
- **How it works:** The React application remains a standard web application running in any browser. The backend spawns Playwright controlling real Chromium (headed in a virtual frame buffer like Xvfb, or headless with CDP screencasting).
- The backend captures the real browser viewport using CDP `Page.startScreencast` or WebRTC and streams the video/canvas frames to the frontend `BrowserViewport`.
- When the user clicks or types in the viewport, coordinates are sent via WebSocket to Playwright (`page.mouse.click(x, y)` / `page.keyboard.type(text)`).
- When the AI agent operates the browser, the user sees real-time typing, clicking, and page navigation live in the stream.
- **Playwright Integration:** Direct and native on the backend.
- **Saarthi UI Impact:** Zero redesign. Only `BrowserViewport.tsx` becomes an interactive `<canvas>` / video stream receiver.

### Option 3: Chrome / Edge Browser Extension (Side Panel API)
- **How it works:** The user runs everyday Google Chrome or Edge. SAARTHI is installed as a Manifest V3 extension. The SAARTHI panel is hosted inside Chrome's native `chrome.sidePanel` API on the right.
- **Drawback:** The browser window, tabs, and address bar are Chrome's native UI, not our custom branded SAARTHI browser chrome. The project requirement specifies our own integrated browser shell.

### Option 4: Tauri v2 Multi-Webview
- **How it works:** Uses OS-native webviews (WebView2 on Windows, WebKit on macOS).
- **Drawback:** Lacks consistent Playwright automation hooks across platforms. Playwright is built specifically for Chromium, Firefox, and WebKit binaries, not OS WebView2/Safari embedding. Automation complexity is significantly higher than Electron or CDP streaming.

---

## 4. How the Existing SAARTHI Panel Remains Unchanged

The SAARTHI AI co-pilot panel is already cleanly decoupled in our architecture:

```text
src/components/saarthi/
├── SaarthiPanel.tsx    <-- Container (fixed 380px right-hand sidecar)
├── SaarthiHeader.tsx   <-- Branding, agent status badge, connection status
├── ChatMessage.tsx     <-- Message bubbles, agent execution trace steps
└── ChatInput.tsx       <-- Prominent Voice Command button, quick pills, textarea
```

### Why it remains completely untouched:
1. **Isolated State & Props:** `SaarthiPanel` receives standard React props:
   - `messages: ChatMessageItem[]`
   - `onSendMessage: (text: string) => void`
   - `activeTabTitle: string`
   - `isAssistantThinking: boolean`
2. **Transport Agnostic:** The panel does not know or care whether the browser is running via an Electron `WebContentsView` or a backend WebSocket stream.
3. **Voice Interaction Preserved:** The primary voice command button and keyboard shortcuts operate solely in the frontend React state and simply emit the final instruction string to the orchestrator.

---

## 5. How Future Playwright / Agent Control Works with the Architecture

Whether using **Electron (CDP attach)** or **Backend Streaming (CDP stream)**, the agent control pipeline follows the exact same standardized loop:

```text
[User] (Voice or Text)
   │
   ▼
[SAARTHI Panel (React)]
   │
   ▼ (WebSocket / IPC)
[Agent Orchestrator] ─── (LLM Decision / Planning)
   │
   ▼ (Playwright API)
[Playwright Engine]
   │
   ▼ (CDP Commands: goto, click, fill)
[Real Web Content (Chromium)] ───► [Viewport in SAARTHI UI (Live)]
```

### Agent Inspection & Grounding:
1. **DOM Extraction:** Playwright captures the accessibility tree (`page.accessibility.snapshot()`) or extracts clickable elements with unique test IDs / bounding boxes.
2. **Visual Action Coordinates:** When the agent decides to click, it issues `page.mouse.click(x, y)` directly on the real webpage.
3. **Simultaneous User Control:** If the user wants to take over manually, clicks on the viewport are forwarded directly to the page.

---

## 6. How Navigation, Tabs, and the URL Bar Connect to Real Pages

Our existing `BrowserToolbar.tsx` has full UI for:
- Back (`ArrowLeft`)
- Forward (`ArrowRight`)
- Reload (`RotateCw`)
- Omnibox Address Bar (`Lock`, URL input, `Enter` submission)
- Tabs (`Google`, `arXiv`, `+` New Tab, Close `x`)

### Event Mapping Contract:

| UI Element | User Action | Triggered Browser Event |
| :--- | :--- | :--- |
| **Address Bar** | User types URL & hits `Enter` | `browser.navigate(tabId, url)` $\rightarrow$ `page.goto(url)` |
| **Back Button** | User clicks Back | `browser.goBack(tabId)` $\rightarrow$ `page.goBack()` |
| **Forward Button** | User clicks Forward | `browser.goForward(tabId)` $\rightarrow$ `page.goForward()` |
| **Reload Button** | User clicks Reload | `browser.reload(tabId)` $\rightarrow$ `page.reload()` |
| **Tab Item** | User clicks Tab | `browser.switchTab(tabId)` $\rightarrow$ activates target page context |
| **New Tab (`+`)** | User clicks `+` | `browser.createTab()` $\rightarrow$ `browserContext.newPage()` |
| **Close Tab (`x`)** | User clicks close | `browser.closeTab(tabId)` $\rightarrow$ `page.close()` |

### Bidirectional Synchronization (Browser $\rightarrow$ UI):
The real browser page must also update the React UI when page state changes:
- **`onUrlChange(url)`**: Updates the Omnibox text and security lock status.
- **`onTitleChange(title)`**: Updates the active Tab title.
- **`onFaviconChange(iconUrl)`**: Updates the tab icon.
- **`onLoadingChange(isLoading)`**: Toggles the reload button into an animated spinner / progress bar.
- **`onNavigationStateChange({ canGoBack, canGoForward })`**: Enables/disables the Back and Forward buttons.

---

## 7. Component Inventory: What Stays vs. What Needs Replacement

| Component | Path | Status | Role in Real Browser System |
| :--- | :--- | :--- | :--- |
| **`SaarthiPanel.tsx`** | `src/components/saarthi/` | **KEEP 100%** | Dedicated AI co-pilot panel container |
| **`SaarthiHeader.tsx`** | `src/components/saarthi/` | **KEEP 100%** | Displays SAARTHI branding, live tab connection status |
| **`ChatMessage.tsx`** | `src/components/saarthi/` | **KEEP 100%** | Renders user prompts, agent step traces, and action pills |
| **`ChatInput.tsx`** | `src/components/saarthi/` | **KEEP 100%** | Prominent Voice Command button, quick pills, text input |
| **`BrowserToolbar.tsx`** | `src/components/browser/` | **KEEP 100%** | Top window chrome, tabs, URL bar, navigation controls |
| **`BrowserSidebar.tsx`** | `src/components/browser/` | **KEEP 100%** | Compact 48px left rail with browser tools & settings |
| **`BrowserShell.tsx`** | `src/components/browser/` | **KEEP 100%** | Root CSS Grid / Flex layout frame |
| **`BrowserViewport.tsx`** | `src/components/browser/` | **REPLACE** | **Replace static Google HTML with real content host** (either native `WebContentsView` mount or `<canvas>` live stream) |

---

## 8. What the AI / Backend Team Needs to Provide to the Frontend

To connect the frontend to the backend agent and Playwright engine, the AI/backend team needs to expose the following contract:

### 1. WebSocket / IPC Gateway:
- A local WebSocket or Electron IPC bridge (e.g. `ws://localhost:8000/ws/browser-session`).

### 2. Events Sent from Backend $\rightarrow$ Frontend:
```typescript
// Tab and Page State
interface PageStateEvent {
  type: 'page:state'
  tabId: string
  url: string
  title: string
  favicon?: string
  isLoading: boolean
  canGoBack: boolean
  canGoForward: boolean
}

// Agent Step Updates
interface AgentStepEvent {
  type: 'agent:step'
  stepId: string
  description: string     // e.g. "Focused search input #APjFqb"
  status: 'in_progress' | 'completed' | 'failed'
}

// Agent Final Response
interface AgentResponseEvent {
  type: 'agent:response'
  message: string         // Formatted markdown summary of findings
  suggestedFollowUps?: string[]
}

// (If using Web Streaming approach)
interface FrameUpdateEvent {
  type: 'stream:frame'
  format: 'webp' | 'jpeg'
  data: ArrayBuffer | string  // Base64 or binary image frame
}
```

### 3. Events Sent from Frontend $\rightarrow$ Backend:
```typescript
// User Navigation Controls
interface NavigateCommand {
  action: 'navigate' | 'goBack' | 'goForward' | 'reload' | 'newTab' | 'closeTab'
  tabId?: string
  url?: string
}

// User Instruction to SAARTHI
interface UserInstructionCommand {
  action: 'agent:execute'
  prompt: string          // e.g. "Search Mumbai Delhi flights and find cheapest"
  activeTabId: string
}

// (If using Web Streaming approach: User manual click/type)
interface UserInputInteraction {
  action: 'input:mouse' | 'input:keyboard'
  type: 'click' | 'mousemove' | 'keydown' | 'keyup'
  x?: number
  y?: number
  key?: string
}
```

---

## 9. Comparison & Detailed Analysis of the Two Top Options

| Evaluation Criterion | Option A: Electron Desktop App Shell | Option B: Web-Based CDP Screencast Stream |
| :--- | :--- | :--- |
| **User Experience** | **True Native Desktop Browser.** Direct hardware acceleration, native text selection, native scroll wheels, zero streaming artifacts. | **Web-Accessible Browser.** Accessible via standard browser tab (`localhost:7319`), video/canvas stream of remote Chromium. |
| **Real Website Compatibility** | **100%** (All websites, audio, video, complex WebGL, cookies, logins). | **100%** (Playwright runs full Chromium; visual rendering is streamed). |
| **Playwright Automation** | Connects directly to Chromium via local CDP (`--remote-debugging-port=9222`). | Runs Playwright natively on backend server. |
| **Latency for User** | **0 ms** (Local render). | **15–30 ms** on localhost (very fast), slightly higher over network. |
| **Frontend Effort Required** | **Low:** Wrap Vite in Electron main process, position `WebContentsView` over viewport coordinates. | **Medium:** Implement WebSocket frame receiver and canvas event coordinate mapper. |
| **Deployment Model** | Desktop executable (`.exe` / `.dmg`). | Standard web application (`http://localhost:7319`). |

---

## 10. Recommended Approach for SAARTHI

### Final Recommendation: **Option A — Electron Desktop Shell (`WebContentsView` + CDP Playwright)**

### Why this is the optimal path for SAARTHI:

1. **Alignment with Product Identity:**  
   SAARTHI is branded as an **AI Browser** (similar in concept to Arc, Dia, and Anthropic's browser-use desktop environment). A desktop shell gives users an authentic standalone browser application with dedicated window controls, OS shortcuts, and true browser feel.
2. **Zero Security/Iframe Obstacles:**  
   Electron's `WebContentsView` completely bypasses `X-Frame-Options` and CSP frame-ancestor blocks while maintaining modern Chromium process isolation. Real Google Search, flight portals, hotel booking engines, and banking sites load identically to standard Google Chrome.
3. **Flawless Playwright Integration:**  
   Electron is built on Chromium. The backend AI agent can launch or attach to Electron's Chromium instance via Chrome DevTools Protocol (`connectOverCDP`) with zero friction. The agent can navigate, click, type, and evaluate DOM selectors while the user watches the native web page update at 60 FPS without streaming compression or video encoding overhead.
4. **Preserves 100% of the Existing SAARTHI UI:**  
   - `SaarthiPanel.tsx`, `ChatInput.tsx`, `ChatMessage.tsx`, `SaarthiHeader.tsx` remain **strictly untouched**.
   - `BrowserToolbar.tsx`, `BrowserSidebar.tsx`, `BrowserShell.tsx` remain **strictly untouched**.
   - Only `BrowserViewport.tsx` is transformed from static mockup HTML into a mount target for the native `WebContentsView`.

### Alternative Fallback:
If the project owner strictly requires SAARTHI to run exclusively inside a web browser tab at `http://localhost:7319` (without installing a desktop app), **Option B (CDP Screencast Stream)** is the only viable alternative.

---

## Summary of Next Steps for Implementation (When Approved)

1. **Review & Sign-Off:** Review this plan with the team and project owner.
2. **Configure Electron Shell wrapper:** Add a lightweight `electron/main.ts` that boots the Vite dev server and mounts the native `WebContentsView` over the central viewport.
3. **Bind Toolbar IPC Events:** Connect the existing address bar, back/forward, and reload buttons to the Electron view's navigation methods.
4. **Establish Backend Agent Bridge:** Expose the CDP remote debugging port so the AI team's Playwright agent can control the live session.
