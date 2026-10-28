# Shopping Plugin AI

Shopping assistant monorepo. The original AI package and the newer API/browser demo
are separate integration layers:

- Python 3.11+ package with a transport-independent `AIRequest → AIResponse` interface.
- Offline mock demo and an optional real Gemini structured-output adapter.
- Grounded product facts, clarification, and validated next-tool proposals.
- [`ai/`](ai/README.md): provider-neutral core, conversation/runtime orchestration,
  checkpoint codec, browser/market/voice contracts and offline scenarios.
- [`backend/`](backend/README.md): FastAPI, anonymous demo or verified JWT authentication,
  snapshots, price history, chat, context, alerts and reviews. PostgreSQL migrations
  and the authenticated core runtime, event ledger, committed browser outbox,
  confirmation/receipt APIs, deadline worker and speech transport live here.
- Local container stack: API, migration job, deadline worker and private PostgreSQL;
  see [local container setup](backend/README.md#local-container-stack).
- [`frontend/`](frontend/README.md): Electron browser UI/demo and API client.

The core is connected to public APIs and durable PostgreSQL state. The local HTTP
demo uses deterministic providers and synthetic browser receipts; it does not launch
a retailer browser or establish live Neon, speech quality or production-account readiness.

## Try it

From the repository root, inside an activated Python virtual environment:

```sh
python -m pip install -e './ai[dev]'
python -m shopping_ai.cli ai/examples/product_question.json --fresh-demo
python -m shopping_ai.cli ai/examples/cart_proposal.json --fresh-demo
python -m pytest ai/tests -q
```

The mock is deterministic test/demo logic, not a language model. The cart example
returns a proposal; it does not click a page or claim a purchase/cart update.
For the integrated local API and synthetic cart/recovery demo, see
[backend setup and runtime integration](backend/README.md#local-ai-runtime).
The existing frontend API client exposes typed runtime methods for the browser team;
its execution-panel UI is still the team's separate browser-executor integration boundary.
