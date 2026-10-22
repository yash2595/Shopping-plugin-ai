# Copilot Browser

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
