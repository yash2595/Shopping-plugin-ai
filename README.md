# Shopping Plugin AI

The latest visual architecture report sets a local-first analytical assistant as
the target: evidence-backed product/review answers and safe browsing skills,
with cart/purchase actions blocked. The existing desktop side panel now captures
supported product specs/reviews and calls a stateless analysis API: exact-source
answers, budget/RAM/storage/colour shortlists, multi-product comparisons and review
excerpts. Highlight/scroll actions recheck native document identity and exact DOM
text. Browser speech has editable transcripts and local-voice playback fallback.
An optional loopback Ollama route and explicit cloud fallback remain available for
the durable runtime. WXT/MV3 packaging and real-device/retailer acceptance are
still pending. See the [phase checklist](backend/README.md#latest-visual-architecture-phases).

Saarthi now opens a shared conversation from the avatar: shopping dialogue, explicit
website/search commands, existing-tab navigation and bounded scrolling. Search
clarifications and explicit preferences survive page changes. Supported retailer
results can be opened from observed product links, followed by source-based answers.
See the [conversation/navigation implementation](frontend/README.md#saarthi-conversation-and-browser-navigation).

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
For the user-facing analysis path, start the backend with `AI_RUNTIME_ENABLED=true`,
configure the supported origins, then run the existing frontend desktop app. Open
an Amazon.in/Flipkart product page, ask about a specification, capture another page
and ask to compare. No model credential is needed for exact evidence analysis.
