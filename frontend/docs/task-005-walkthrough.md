# TASK #005: SAARTHI Frontend Interaction Panel

## What changed

The SAARTHI side panel is now voice-first. The text area, send button, quick prompts, and suggested-action pills have been removed from the active UI.

- The lower panel contains one prominent microphone control.
- It renders `idle`, `listening`, `processing`, `executing`, `completed`, and `failed` visual states.
- The center of the panel is a white-box execution timeline with pending, running, completed, and failed rows.
- The current running step is surfaced separately in a `CURRENT ACTION` card.

## Frontend event contract

`src/types/agentEvents.ts` defines the append-only `AgentEvent` contract for future transports. It supports:

- `agent_started`
- `action_started`
- `action_completed`
- `action_failed`
- `navigation`
- `page_loaded`
- `agent_completed`

Events can carry an action, step id, tab id, URL, timestamp, and extensible metadata. `src/utils/agentExecution.ts` derives the timeline and current action from those events.

## Demo flow

In development, press the microphone button to run the local flight-search demonstration. It visibly progresses through listening, processing, navigation, field-entry, search, and completion. The small `Test failed state` control is development-only and exists solely to demonstrate the failure UI.

No voice capture, browser control, AI response generation, or network transport is performed by this demo.

## Future integration

The backend/AI transport should append `AgentEvent` values to the frontend event stream. The panel will derive execution visibility from those events without needing a UI redesign.
