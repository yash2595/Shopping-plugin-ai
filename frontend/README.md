# Copilot Browser

## Shopping assistant flow

The side panel now has a typed shopping question, editable browser-mic transcript,
source-backed answer cards, a comparison table, observed-price shortlist and local
voice read-aloud. Ask on each supported product page to retain up to four captures,
then compare them. Explicit budget/RAM/storage/colour requirements stay in memory;
Clear removes them and the captures. Stale/unknown values do not become invented
facts or recommendations. This is a captured-evidence assistant, not global web
search or an automatic purchasing agent.

Start the backend on port 8000 with `AI_RUNTIME_ENABLED=true` and the Amazon.in /
Flipkart origins in `AI_BROWSER_ORIGINS` (see the backend `.env.example`). From
this folder, install normal desktop dependencies and run `npm run start:electron`.
The Electron binary must be installed; `npm ci --ignore-scripts` is sufficient for
CI tests/bundling but does not install that binary. HTTPS public product pages are
supported; cart, account, sign-in and checkout pages are not capture sources.

The packaged app's JSON calls use a trusted main-frame HTTP bridge, so backend CORS
does not need to permit `null` origins. `SHOPPING_BACKEND_URL` chooses a trusted
backend origin in the main-process environment, defaulting to localhost:8000.
Remote origins must use HTTPS. The bridge forwards its in-memory bearer token,
never retailer cookies, refuses redirects and bounds request/response sizes.

Show on page highlights a spec row or scrolls to an exact captured review excerpt
only after native active-document/source/text checks. No model selectors, generated
scripts, cart click or purchase instruction is accepted. Missing selectors/text
produce a visible failure rather than a success claim. Product/review extraction
uses packaged bounded selectors; live retailer layouts still require acceptance.

Mic is feature-detected and never auto-submits: edit the transcript before Ask.
Browser recognition may use online services; failure keeps typed input usable.
Read aloud uses an available `localService` voice; failure keeps the answer visible.
Stop, a new question and tab/panel changes discard late answers/playback. Actual
microphone/voice quality and device permissions have not been certified by builds.

Release renderers retain sandboxing and privileged IPC checks the main frame.
Remote debugging is off unless a development port is explicitly configured.
`SHOPPING_UNSAFE_DEV_SANDBOX=1` is an opt-in managed-host development workaround;
it is ignored by packaged builds and is not a release configuration. The overlay's
incorrect `BrowserWindow.getURL()` call was removed and native TypeScript now checks.
WXT/MV3 packaging, offscreen VAD, rating-filter clicking and real-device/retailer
acceptance remain separate work; they are not claimed as completed here.

A modern, high-performance desktop browser with an adaptive AI assistant overlay and White-Box Execution timeline, built with Electron, React, and TypeScript.

---

## 🌟 Overview

**Copilot Browser** is a desktop application that combines standard multi-tab web browsing with an observable AI assistant:

- **Chromium WebContentsView Architecture**: Native web browsing powered by Electron's modern `WebContentsView` API, delivering full web compatibility and performance.
- **Dynamic Viewport Bounds Synchronization**: Real-time layout tracking via `ResizeObserver` ensures active native web views dynamically adapt when side panels expand or contract.
- **Floating AI Assistant Overlay**: A transparent, frameless BrowserWindow overlay that displays an adaptive, multi-state assistant orb (supporting idle, listening, processing, executing, completed, and failed states) with silent video animation.
- **White-Box Execution Timeline**: An observable execution panel displaying real-time browser actions, navigation events, step progress, and intent verification.
- **Microservice Integration Ready**: Clean typed integration interfaces (`src/types/assistant.ts`) designed to connect with external AI decision engines and backend services without tight coupling.

---

## 🤝 Ecosystem & Team Architecture

This project is the **Desktop Browser Host** for an AI-assisted shopping system:

- **Browser Host (This Repository)**: `copilot-browser` — Electron + React + TypeScript desktop interface.
- **AI Decision Engine**: [`Shopping-plugin-ai`](https://github.com/yash2595/Shopping-plugin-ai.git) — Python package providing grounded product decisions, facts validation, and next-step proposals.

Integration between the browser host and the AI engine occurs over structured HTTP / WebSocket contracts (`AIRequest → AIResponse`), avoiding code duplication across language stacks.

---

## 🛠️ Tech Stack

- **Desktop Shell**: Electron 44 (Chromium `WebContentsView`, context isolation, sandboxed renderers)
- **Frontend**: React 19, TypeScript, Tailwind CSS v4
- **Build System**: Vite 8, esbuild, oxlint
- **Remote Debugging**: Chrome DevTools Protocol (CDP) enabled on port `9222`

---

## 🚀 Getting Started

### Prerequisites

- [Node.js](https://nodejs.org/) (v20+ recommended)
- `npm`

### Installation

```bash
git clone https://github.com/<your-username>/copilot-browser.git
cd copilot-browser
npm install
```

### Available Scripts

| Command | Description |
| :--- | :--- |
| `npm run dev` | Starts the Vite development server on `http://localhost:7319` with Hot Module Reloading (HMR). |
| `npm run build` | Runs type checks (`tsc -b`) and bundles the React frontend using Vite. |
| `npm run build:electron` | Bundles Electron main and preload scripts into `dist-electron/` using esbuild. |
| `npm run electron:dev` | Launches Electron connected to the local development bundle or production fallback. |
| `npm run start:electron` | Performs a full build (`npm run build` + `npm run build:electron`) and launches Electron. |
| `npm run lint` | Runs `oxlint` for high-performance static analysis. |
| `npm run preview` | Previews the Vite production build in a local static server. |

---

## 📂 Project Structure

```text
├── electron/
│   ├── main.ts            # Electron main process (WebContentsView management, bounds sync, overlay window)
│   └── preload.ts         # ContextBridge security barrier exposing safe electronAPI
├── src/
│   ├── components/
│   │   ├── browser/       # Browser UI chrome (BrowserToolbar, BrowserSidebar, BrowserViewport, BrowserShell)
│   │   └── saarthi/       # Assistant overlay & panel (SaarthiAvatar, SaarthiHeader, SaarthiPanel, ExecutionTimeline)
│   ├── types/
│   │   ├── agentEvents.ts # Observable execution event and step types
│   │   ├── assistant.ts   # Clean integration contracts for STT, TTS, AI Engine, and backend status
│   │   ├── browser.ts     # Multi-tab and browser state interfaces
│   │   └── electron.d.ts  # Typed electronAPI window augmentation
│   ├── App.tsx            # Main application coordinator
│   └── main.tsx           # React renderer entrypoint
├── public/
│   └── avatar/            # Assistant visual assets and speaking video loops
├── index.html             # Main browser window document
├── orb-overlay.html       # Transparent assistant overlay window document
├── vite.config.ts         # Vite multi-page configuration (main + orbOverlay)
└── package.json
```

---

## 🔒 Security & Renderer Isolation

- `nodeIntegration: false` and `contextIsolation: true` enforced on all renderer windows.
- Native web browsing operates in sandboxed `WebContentsView` instances with isolated storage and navigation handlers.
- Inter-Process Communication (IPC) is strictly gated through typed contextBridge handlers in [`electron/preload.ts`](electron/preload.ts).
# AI runtime transport integration

The existing `ShoppingApiClient` now exposes the versioned `/v1/ai` conversation,
event, confirmation, work claim/receipt, audio, speech and deletion methods. See
`../backend/README.md#local-ai-runtime` and `../backend/contracts/openapi.json` for
the full local setup and public request/response contract. The SDK uses the existing
token vault; logout revokes the server token, and policy/site 403 responses preserve
valid identity. It never automatically retries or renumbers a browser mutation.

The analytical panel, packaged product adapters, native foreground/document checks
and browser voice controls are connected. A durable local mutation journal remains
required before enabling any future cart executor. Renderer tab labels/URLs are not authentication
or document-lifetime IDs. Persist stable event IDs, claim IDs and outcomes before
executing work; on reconnect load the saved conversation/work, and reuse the existing
journal entry rather than clicking a claimed mutation again. Only the explicitly
reviewed confirmation DTO can create server-side mutation permission.

`sendAiAudio` uploads bounded raw media with event/version headers.
`synthesizeAiSpeech` returns a Blob and validated conversation version; the caller
must recheck its active turn/version and stop stale playback on new input/cancel/logout.
Without configured server speech, browser STT can send its edited transcript through
`sendAiEvent`; normal text remains usable. No production provider, real retailer cart
acceptance or native executor wiring is implied by the local SDK tests/build.
# Verified identity integration

For an account-backed deployment, supply the selected login integration's secure
`tokenProvider` and `allowAnonymousRegistration: false` to `ShoppingApiClient`.
Without a token, requests fail with a sign-in error rather than creating an
anonymous account. The backend verifies API access tokens against its configured
issuer, audience and public signing keys. Login, refresh and provider sign-out
belong to that selected identity integration. Never store private keys or backend
secrets in the renderer. `getAiCapabilities()` reads authenticated runtime settings
and limits; its flags are configuration information, not live provider health.

The latest analysis/local-first backend profile adds `analysis_only`,
`local_fallback` and `provider: 'local'` to capabilities. Each reply includes
`inference_fallback: 'deterministic' | 'cloud' | null`; show a degradation badge
from that committed reply rather than guessing provider health. These are typed
integration fields for the durable runtime. The separate stateless analysis UI is
now connected as described above. Analysis
mode does not permit variant/cart dispatch, even when the historical mutation
flag is enabled.

## Saarthi conversation and browser navigation

The avatar opens one conversation panel for typed questions and edited speech
transcripts. Up to 40 turns and explicit budget/RAM/storage/colour preferences stay
in app memory across page/tab changes. Clear resets that local memory. Non-product
shopping dialogue uses the existing authenticated, versioned backend conversation;
page content and model replies never enter the browser-command planner. Backend
conversation records follow backend retention, independently of local Clear.

Direct user commands support public HTTPS websites, Amazon/Flipkart/Google search,
back/forward, refresh, bounded scrolling and existing-tab switching. Examples:
`Amazon kholo`, `search headphones` → `Amazon par`, `अमेज़न पर हेडफोन खोजो`,
`open first result`, `scroll down`, `next tab`, then `battery kitni hai?`. Result
opening supports the first five parsed Amazon/Flipkart product links on search
pages; it canonicalizes product URLs and drops tracking rather than clicking a
page control. Other websites can be opened, but arbitrary page understanding,
forms, login, booking and universal website automation are not implemented.

The trusted native executor checks the foreground tab/source and validates commands
again. Page-load events acknowledge navigation; failures/timeouts/Stop do not claim
completion. Stop/tab/manual navigation cancels pending load listeners; pending
result reads recheck document identity. No generated scripts/selectors, checkout,
cart action or silent page-triggered navigation are accepted by this executor.
Listening state follows recognition events and local read-aloud drives the avatar's
speaking animation. Voice remains push-to-talk with editable transcripts; always-on
VAD and general Hindi language understanding are not claimed.

Verification: 43 client tests, TypeScript/Vite build, native TypeScript check and
Electron bundle passed. A real Electron hidden-window smoke used intercepted HTTPS
fixture pages and local PostgreSQL/API: avatar activation, greeting, search follow-up,
foreground rejection, exact battery answer, Clear, two-turn backend conversation and
conversation-budget-to-product-shortlist all passed. Native executable fixture tests
cover successful navigation, result extraction, failure, stale targets and Stop.
Focused visible-window navigation, live retailer selectors, microphone/local voice
and installed local-model quality still need device acceptance. No live retailer
or paid model request was used.

## Data controls and maintenance

Data and privacy explains backend/model routing before any analysis capture or
backend shopping request. Allow shopping analysis is unchecked by default and
lasts only for this app process; browser speech has its own unchecked permission
(which resets when its panel unmounts). Local greeting/navigation remains usable
without either permission. Withdrawal stops local tasks and clears local memory.
Clear alone does not erase server records.

Delete stored shopping data has a second confirmation and preserves account/auth
and quota metadata. It removes owned active-database shopping records and pending
AI work, not backups, external-provider records or the identity-provider account.
Check AI usage is an explicit authenticated read, not a background telemetry loop.
Its counts represent admitted turns; real provider call/token costs may differ.
Report a page-reading problem creates a local, manually shareable diagnostic
without addresses, account/product identifiers, page text or secrets.

Daily scheduled CI exercises existing offline DOM/navigation fixtures. It provides
regression coverage and does not check live retailer layouts. The app neither
adds bulk server scraping nor bypasses blocking/challenges. This remains a free
desktop preview with paid plans/mobile/MV3 packaging outside this implementation.

## Separate navigation orchestration

Navigation now has a separate planner/controller/executor path instead of the old
single-command-only wiring. `NavigationOrchestrator` accepts a complete validated
plan, observes the native foreground tab/document before each step, dispatches one
step with a stable execution ID, checks the resulting native URL/document, and only
then runs the next step. The panel displays each step and its status. Example:
`Amazon kholo aur headphones search karo phir pehla result kholo`. Rules handle
known commands locally; ambiguous natural navigation can use the authenticated
backend navigation-planning role after analysis permission. Product facts/review
answers still use their existing analytical/conversation roles.

Limits are eight steps, a two-minute run deadline and a bounded native step wait.
Pause completes the current step and stops before the next; Resume re-observes the
same target and skips completed steps. Stop cancels remaining steps, stops native
loading and fences late replies. Unknown outcomes, changed documents/targets,
unverified redirects and failed steps do not automatically retry. A fresh user
request is needed after an uncertain result. Native execution IDs are replay-safe
within this app process; they are not a persistent restart journal. Plan state and
native replay records are held in memory, bounded, and reset when the app exits.

The existing durable `/v1/ai` work channel is also consumed rather than replaced
with the old message asking the user to issue an explicit command. It claims owned
work, reads products or navigates to offers/search pages, and submits typed receipts.
All mutation tools are refused. Offer navigation requires matching brand/model,
condition and requested attributes from observed specs; missing fields produce a
failed receipt, not an assumed match. A search adapter without verified offer
extraction reports blocked coverage with no offers and makes no cheapest-price
claim. Cross-store goals retain the existing identity-clarification dialogue.

Native context exposes WebContents/document identity to the trusted app UI only.
Every orchestrated step carries its expected document ID as well as source URL;
reloads of the same URL are therefore detected. The main-process replay journal
returns a previous result for an identical execution ID/body and refuses ID reuse
with a different action. It does not execute model-generated code or selectors.

The backend planner receives direct user text, an optional known current-site name
and permitted tab IDs—not page HTML, cookies, page text or browser history. Model
output is schema-checked and policy-checked again before entering the controller.
`AI_LOCAL_NAVIGATION_MODEL` and `AI_CHAT_NAVIGATION_MODEL` can select a separate
planner model; blank values reuse the current provider's configured model. Setting
a name does not install or download it. Cloud fallback remains explicitly opt-in.

Latest checks: 639 core tests, 182 backend tests and 64 client tests passed (885).
TypeScript/Vite, native TypeScript and Electron bundles passed. The hidden Electron
fixture smoke exercises real HTTPS-intercepted DOM/native loads and the local API: a
three-step website/search/product journey, a separately planned backend goal, Stop
during a delayed load, plus prior privacy/conversation/data-erasure flows. Focus is
explicitly simulated for successful execution; the real unfocused guard is checked
separately. Live visible-window/retailer/device/model quality acceptance is pending.
