# TASK #006 Walkthrough — SAARTHI Adaptive Voice-First Assistant

## Overview

TASK #006 redesigned SAARTHI into an adaptive, voice-first browser assistant. The browser webpage remains full-width by default, and SAARTHI appears dynamically only when activated.

---

## Key Lifecycle States & Behaviors

### 1. Idle State
- **Full Width Webpage**: The browser viewport and Electron `WebContentsView` occupy the maximum available width by default.
- **Side Panel Closed**: The SAARTHI execution panel remains closed in the idle state.
- **Floating SAARTHI Orb**: A single compact, spherical orb floats at the bottom-right (`bottom-6 right-6`).
  - **Color & Gradient**: Dark navy blue (`#040918` / `#0b193d`) to electric light azure (`#38bdf8`) gradient with an inner highlight glint.
  - **Particles & Glow**: Glowing blue sparkle specks with subtle ambient luminescent aura (`animate-saarthi-glow` and `animate-saarthi-float`).
  - **Clean & Futuristic**: No text inside the idle orb; does not obstruct webpage content.

### 2. Activation & Listening State
- Clicking the floating orb transitions SAARTHI into the active state.
- The orb smoothly scales into a prominent central sphere with expanding concentric sonar wave rings (`animate-saarthi-ring-1`, `animate-saarthi-ring-2`, `animate-saarthi-ring-3`).
- Displays a clean `"Listening..."` indicator and a minimal 5-bar audio frequency visualizer.
- No text boxes, no conversational fillers, and no generic chatbot UI.

### 3. Processing State
- Orb transitions into an understanding aura.
- Renders the concise **Voice Request** (`"Find flights from Mumbai to Delhi for next Monday"`) and **Parsed Intent** (`"Search and compare one-way flights from Mumbai (BOM) to Delhi (DEL)"`).
- Zero suggestion pills, quick prompts, or conversational filler.

### 4. Executing State
- Central orb gracefully retracts.
- SAARTHI execution panel slides in from the right (`370px–400px`).
- **Dynamic WebContentsView Resizing**: Electron `WebContentsView` bounds automatically shrink to accommodate the panel while preserving resize observer synchronization.
- **Observable Execution Display**:
  - **Voice Request & Intent Card**: Clearly states what the agent is doing.
  - **Current Action Card**: Real-time spinner with action tag (`TYPE`, `CLICK`, `NAVIGATE`, `EXTRACT`) and concise status text (`Type "Delhi (DEL)" into Destination`).
  - **Observable Timeline**: Clean timeline tracking observable actions (`NAVIGATE`, `PAGE_LOADED`, `TYPE`, `CLICK`, `EXTRACT`).
  - Strict absence of chain-of-thought, hidden reasoning, or conversational fluff.

### 5. Completion State
- Renders an emerald completion card (`"12 flight options retrieved successfully and ready on page"`).
- Features a `"Done (Close Panel)"` button.
- When closed, the panel collapses, the browser expands back to 100% full width, and the floating orb returns to the bottom-right idle position.

### 6. Failure State
- Renders a concise rose failure card (`"Action could not be completed"`).
- Graceful, user-understandable failure message without technical stack traces.
- Includes `"Retry Demo"` and `"Dismiss"` options.

---

## Preservation of Existing Architecture & Contracts

- **AgentEvent Contract**: Uses the existing append-only schema in `src/types/agentEvents.ts` without changes or parallel systems.
- **Electron Architecture**: Electron `BrowserWindow`, `WebContentsView`, preload IPC bridge, tabs, and `ResizeObserver` viewport synchronization are fully preserved.
- **Strict UI Cleanup**: All text composers, send buttons, quick prompts, and chatbot bubbles have been removed from the SAARTHI UI.

---

## Electron Full-Width Viewport Bounds Bug Fix

### Diagnosis & Root Cause
- `electron/main.ts` previously held a hardcoded initial bounds definition (`width: 980, height: 812`), which was sized for the previous 3-column static layout with the right panel always open.
- When starting Electron with the SAARTHI panel closed by default, the `WebContentsView` initialized to 980px, leaving an empty black area between x=1028 and the right edge (x=1440).

### Fix Applied
1. **Dynamic Initial Bounds**: Updated `createWindow()` in [`electron/main.ts`](file:///f:/SAARTHI/electron/main.ts) to calculate `currentBounds` dynamically from `mainWindow.getContentSize()` (`width: Math.max(10, contentWidth - 48)`, `height: Math.max(10, contentHeight - 88)`).
2. **Immediate Layout Synchronization**: Enhanced `useLayoutEffect` in [`BrowserViewport.tsx`](file:///f:/SAARTHI/src/components/browser/BrowserViewport.tsx) with `requestAnimationFrame` and timer callbacks to guarantee exact bounds reporting to Electron upon mounting and panel transitions.
3. **Automatic Dev Server Retry**: Added retry handling for dev server loads in `electron/main.ts`.

---

## Verification & Build Status

- `npm run lint` (`oxlint`): **Passed** (0 errors).
- `npm run build` (`tsc -b && vite build`): **Passed** (built production bundle cleanly).
- `npm run build:electron` (`esbuild`): **Passed** (bundled `main.cjs` & `preload.cjs`).
