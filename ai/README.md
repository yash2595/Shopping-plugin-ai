# Standalone shopping AI

This folder is a real installable Python AI decision package. It runs independently
of the future FastAPI backend, Neon and Chrome extension. The first milestone is:

**message + current product snapshot → bounded context → provider decision →
application validation → grounded answer, question, or tool proposal**

## What works now

| Capability | Current behavior |
| --- | --- |
| Product questions | Select known fact IDs; render exact observed price, stock, variant and specs. |
| Missing/stale evidence | Request a safe product read or clarification. |
| Variant/cart intent | Validate a proposed step against capabilities, target, price/stock and a per-request caller grant. |
| Offline demo | Deterministic keyword-based `MockProvider`, clearly separate from Gemini. |
| Real model adapter | Gemini JSON-schema decision generation, timeout, redacted API errors; optional dependency. |
| Safety boundaries | Unknown tools/arguments/fact IDs rejected; no executable model code or automatic tool execution. |
| Integration contract | Async `ShoppingAI.decide(AIRequest) -> AIResponse`; no web framework dependency. |
| Dialogue | Greeting, requirements, product references and speech-ready reply plus action via `Conversation`. |

`ShoppingAI` performs one model decision; `Orchestrator` composes decisions with
validated extension observations using explicit checkpoints. Persistence, auth,
Neon, real browser execution and voice are not connected. Review synthesis and
free-form conversational answers remain later AI milestones.
Answer values are grounded in supplied observations, not independently verified
retailer claims. Source facts themselves can be incorrect or contain hostile text.
They must be rendered as text, never HTML. Fact selection can still be irrelevant;
schema validation alone does not establish semantic correctness.

## Install and run (Python 3.11+)

From the repository root:

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux instead:
# source .venv/bin/activate
python -m pip install -e './ai[dev]'
python -m shopping_ai.cli ai/examples/product_question.json --fresh-demo
python -m shopping_ai.cli ai/examples/cart_proposal.json --fresh-demo
python -m shopping_ai.cli ai/examples/missing_product.json
python -m pytest ai/tests -q
python -m ruff check ai
python -m ruff format --check ai
```

`shopping-ai` is also installed as a console command. Example fixtures contain
fixed timestamps. `--fresh-demo` refreshes timestamps ONLY for mock fixtures so
the examples do not silently expire. It is forbidden for Gemini; real snapshots
must carry their genuine capture times. Exit codes: 0 for an answer/question/tool
proposal/cancellation, 1 for a blocked/error result, 2 for invalid input/config.

## Real Gemini setup

```sh
python -m pip install -e './ai[gemini]'
```

Set environment variables in your terminal (never commit credentials):

```powershell
# Windows PowerShell
$env:AI_PROVIDER = "gemini"
$env:AI_MODEL = "<tool/structured-output-capable-model-in-your-account>"
$env:GEMINI_API_KEY = "<your-local-key>"
python -m shopping_ai.cli path/to/fresh-request.json
```

```sh
# macOS/Linux
export AI_PROVIDER=gemini
export AI_MODEL='<structured-output-capable-model-in-your-account>'
export GEMINI_API_KEY='<your-local-key>'
python -m shopping_ai.cli path/to/fresh-request.json
```

`.env.example` documents these settings; the package **does not auto-load .env**.
Use your backend's settings loader later. No keys are needed for the mock.
Optional environment limits: `AI_TIMEOUT_SECONDS`, `AI_MAX_CONTEXT_CHARS`,
`AI_MAX_SNAPSHOT_AGE_SECONDS`, `AI_MAX_STEPS`, `AI_MAX_OUTPUT_TOKENS`,
`AI_MAX_CONCURRENT_CALLS`, `AI_CAPACITY_WAIT_SECONDS`.
Invalid/blank settings fail configuration validation instead of silently falling back.
Gemini requires a nonblank model name; credentials remain the provider's responsibility.
The model name is deliberately configurable; model availability, quotas, free-tier
terms and schema support vary. Real calls can incur charges/send supplied data to
the provider. No live model call was required to build/test this package.

The adapter uses the official `google-genai` SDK with `response_json_schema`.
This first version uses structured **next-step proposals**, not native Gemini
function-call continuation or automatic SDK function execution. It avoids giving
the model callable Python/browser functions. You can replace the provider without
changing the request/response contract.

## Files and responsibilities

```text
src/shopping_ai/
  models.py         Pydantic input/decision/output contracts
  config.py         Bounded settings, explicit environment factory
  context.py        Allowlisted facts + six recent messages + versioned rules
  providers/
    base.py         Async provider protocol and typed errors
    mock.py         Offline demonstration, not production NLU
    gemini.py       Real SDK call and redacted provider failures
  policy.py         Deterministic tool and mutation checks
  engine.py         One step, bounded concurrency, timeout and grounded rendering
  browser.py        Visible-execution envelopes and typed browser observations
  orchestrator.py   Owner-bound checkpoint transitions, result checks and recovery
  intents.py        Generic meaning/requirements contract and conservative offline fallback
  conversation.py   Owned dialogue checkpoints, follow-ups and reply/action coordination
  comparison.py     Deterministic comparison of fresh normalized product evidence
  cli.py            JSON fixture runner
examples/           Price/colour, cart proposal, missing product
tests/              Contract, policy, freshness, errors and mocked SDK tests
```

Context is capped in **characters**, not tokens. Old history is removed first;
relevant facts/task constraints are never silently truncated. Oversized remaining
context is blocked. Default limits: 24,000 context characters, 1,000 output tokens,
30-second call timeout, snapshot age up to 120 seconds, and 8 caller-counted steps.
No repair loop or provider retries are enabled. Limits are engineering
settings, not a provider reliability guarantee.

## Backend connection later

```python
from shopping_ai import AIRequest, create_ai

ai = create_ai()  # reads explicit provider/model environment

# Inside an authenticated backend service:
request = AIRequest.model_validate(trusted_input)
response = await ai.decide(request)
# Save task/result, then map response to your API schema.
```

| Response status | Caller responsibility |
| --- | --- |
| `answered` | Persist/render facts with source snapshot IDs. No mutation success is implied. |
| `needs_input` | Display the question and collect a new user turn. Treat model text as text. |
| `tool_proposed` | Bind a durable task/action ID and expiry; persist BEFORE dispatch. Validate browser target again. |
| `blocked` | Show the code, refresh evidence or obtain missing authorization; do not execute. |
| `error` | Surface a recoverable provider problem if retryable; do not fabricate a fallback answer. |
| `cancelled` | Stop new dispatches; reconcile any browser action already in flight. |

`request_id` is correlation, **not** authentication or deduplication. The caller
must enforce owner access, serialize mutations, persist/increment step counts,
deduplicate requests/actions and handle cancellation. This package has no shared
mutable session state, background workers, DB transactions or browser effects.
Each decision validates a private request copy before inference, so caller edits to
nested collections during the await do not alter that step. Invalid input raises
Pydantic `ValidationError` before a provider call; provider/output failures return
an `AIResponse`. Booleans must be actual booleans, and tool lists must be distinct.
Response validation enforces status/field consistency: answers need facts, mutation
proposals need a document binding, and only error responses may be retryable.

### Mutation authorization is a trusted caller input

`MutationAuthorization` binds the **request ID**, permitted tool names, product,
document, variant, quantity, quantity mode, unit-price ceiling, currency and expiry.
The future backend must construct it from explicit user intent after resolving
ambiguity. Never copy a client/model-supplied grant into the trusted request.
The grant is a permission ceiling, not proof the latest message requests a mutation.
Prompt instructions help selection, but semantic user-intent resolution remains a
caller responsibility. Create a new grant for each turn; do not reuse it as memory.

- `ensure_quantity=1`: target total is one matching cart unit.
- `add_quantity=1`: increment existing matching quantity by one.
- `max_unit_price`: unit price only, not delivery fees/tax or full order total.
- Variant changes require a new snapshot with updated price/stock before a cart step.
- All observations must use timezone-aware timestamps; future observations are rejected as stale.
- Proposal validation cannot prevent a site changing after the check. The browser
  executor must re-read identity, offer and preconditions immediately before acting.
- Unknown cart outcome must trigger a read/reconciliation before retry. That durable
  action loop belongs to the future backend; do not repeatedly call this planner
  as though it were an executor or an exactly-once cart system.

There are intentionally no checkout/payment/search tools yet. No connection to
Neon or extension is implied by these proposals.

## Tests and next milestone

The suite tests malformed provider output, unsupported scripts/tools, stale and
future snapshots, product/variant/quantity mismatch, grant scope/expiry, price/stock
checks, cancellation, deadlines and error redaction. SDK request construction is
tested with an in-memory fake client; this does not prove live account compatibility.
GitHub Actions runs lint, formatting, tests and mock demos on Python 3.11–3.13.

Next (after approval): connect a real retailer's read-only product snapshot to this interface;
then add a backend-owned task/action store and a verified browser executor. Add
free-form, evidence-backed summaries only with separate claim evaluation.

Official references: [SDK](https://googleapis.github.io/python-genai/),
[structured output](https://ai.google.dev/gemini-api/docs/structured-output).


## Orchestration and visible browser execution

`Orchestrator` stores no runs internally. Its methods accept an authenticated
`Principal`, a trusted saved `RunState` and `expected_version`; they return a new
checkpoint. Principal/run checkpoints must never be deserialized directly from
an untrusted client's claims. The future backend owns their authority.

| Method | Transition / output |
| --- | --- |
| `start` | Create an isolated, JSON-serializable checkpoint (not a DB record). |
| `advance` | One bounded step; answer/clarify, or return one pending browser action. |
| `accept_result` | Validate action/run/session/version/tab/document/time binding; verify observed product/variant/cart. |
| `pause` / `resume` | Pause a ready run; resume only the same bound tab/document. |
| `cancel` | Stop future steps; retain in-flight action binding for a late receipt. |
| `expire_pending` | Lost/expired action pauses; cart mutations become uncertain, never auto-replayed. |

A browser action requires foreground execution, target highlighting, smooth
scrolling and visible typing. These are **executor requirements**, not an
implementation of a browser UI. Packaged extension adapters must perform the
scroll/type/click sequence appropriate to the semantic tool, verify current
focus/document/expiry immediately before acting, and stop on manual interference.
No arbitrary selector, script or URL is accepted as an AI argument. Generic
navigation/search/typing tools are not enabled by this release; that needs a
separately approved action registry and target validation. No Playwright or
Browser Use dependency has been added to the AI package.

Cart proposals first request a fresh baseline read. `ensure_quantity` can finish
without a click when the observed baseline already matches. `add_quantity` checks
the exact baseline plus increment. Only a matching product/variant/quantity in a
fresh post-action observation completes a cart task. Unknown/failed/expired cart
mutations pause; after explicit resume the only next operation is a safe cart
read. Unresolved reads stay paused; no Add replay. Step budgets also bound these
recovery attempts. A cancelled task records late results without becoming active.
The backend must separately arrange any needed reconciliation for a cancelled
mutation, since this orchestrator will dispatch no further actions from that run.

`last_verified_tool` is trusted observation context set by the orchestrator, not
model authority. A repeated successful variant selection pauses instead of
clicking again. A clarification requires a new user turn/run; this initial API
has no automatic checkpoint rewriting to change goals/grants. Unexpected document
changes pause rather than transferring an old mutation grant to a different page.

### Concurrency and integration responsibilities

1. **Persist with compare-and-swap before dispatch.** Update the row only when
   stored version equals the input version. Two callers can compute different
   candidates from the same checkpoint; only the committed winner may dispatch.
   The local `expected_version` check cannot detect a stale DB checkpoint by itself.
2. **Cancellation wins through the same CAS.** A model response computed before
   cancellation must be discarded if its input version is no longer current.
3. Deduplicate action IDs in the executor and receipt store; accept only receipts
   authenticated to that owner/session. IDs are correlation, not authentication.
   External website effects cannot be made exactly-once with a local version check.
4. Reuse one `ShoppingAI` per worker/event loop. It limits active provider calls
   (default 8); capacity waits time out (default 2 seconds) with retryable
   `AI_CAPACITY`. Always release capacity after timeout/cancellation. These limits
   are per instance, not distributed quotas or per-user fairness.
5. Backend ingress limits, identity verification, DB locking/CAS, durable storage,
   cross-process quotas and real load testing remain backend/deployment work.
   Concurrent isolated mock tests do not certify production capacity.

Offline simulated-executor example (no browser or real cart is touched):

```sh
python ai/examples/orchestrated_cart.py
```

It runs baseline read → proposed cart action → simulated observation → verified
checkpoint. To integrate later, replace simulated results with the extension's
observations and persist every transition. Never send a pending action until its
checkpoint is committed, and never treat an AI proposal as a completed action.


## Conversation-first AI (provider-independent)

`Conversation(Orchestrator(ai), interpreter=...)` adds the dialogue above the
existing decision/action core. It never imports an API SDK, web framework or voice
provider. `start(principal)` returns the opening greeting without a model call;
call it only for a new conversation. Restore its saved state on reconnect instead
of creating another conversation. Existing sessions do not share a global store.

`respond(state, principal, ConversationTurn(...), expected_version=...)` returns a
`ConversationReply` with the next checkpoint, display `message`, `speech_text`, and
optional bound browser `action`. Product answers retain their `facts` and source
snapshot IDs. TTS playback and actual navigation are the
frontend/executor's job. Persist using CAS **before** delivering a returned action.
A pending task is not overwritten by a new goal. Fresh read-only product questions
and greetings can still be answered without dispatching a second action. Stop cancels future dispatch;
late receipts can still be recorded without restarting that task.

Capabilities in this increment:

- Collect category/budget, retain use case/colour/size when interpreted, and record
  which user turn supplied each slot. Later values can replace earlier values.
- Resolve selected/first/second/third/previous products against a bounded stable
  list. Multiple candidates or unavailable references ask instead of guessing.
- Explain fresh saved product evidence without unnecessary navigation. A stale
  product needs a fresh read; mutations also require the current page binding.
- Return natural progress text **with** a browser action. It says an action is
  underway, not that it succeeded. Verified cart observations produce completion.
- Never create mutation grants from extracted slots. Existing trusted grants are
  checked; a stored budget can only narrow their unit-price ceiling. An explicit
  colour that conflicts with the authorized variant asks for clarification.
- Bound history to 12 messages and products to 10; retain 50 recent request IDs to
  reject immediate turn replay. Backend durable idempotency remains required.

The default `BasicInterpreter` is a deliberately limited, deterministic fallback,
not general NLU or an LLM. It covers explicit English/Hinglish shopping phrases,
several categories/colours and INR-style budget amounts (including `2k`). It leaves
negated, ambiguous and compound goals unclear rather than executing part of them.
Currency is a caller-provided conversation setting; foreign-currency conversion is
not performed. Search/discovery, review synthesis and arbitrary
navigation are not implemented by this layer; it reports those limitations.

For richer understanding, inject an async `Interpreter` returning a validated
`Meaning` (intent, requirement patch, selected/previous/ordinal reference). That
interface is model-neutral; this release adds no provider/API integration.
Slot extraction is conversational context, not independent proof of user consent.
The existing backend-grant contract still applies to all mutations.

For action receipts, call `Conversation.accept_result(...)`. If it returns
`working`, call `Conversation.advance(...)` for the next bounded step. A paused run
can be resumed with `Conversation.resume(...)` and a fresh trusted target.
Use `Conversation.expire_pending(...)` when an action receipt never arrives;
expiry does not imply the website action failed. Never rewrite client checkpoints. Persist each run and
conversation transition under the same owner with version checks. Ownership
checks inside these pure objects are not a substitute for backend authentication.

Offline demonstration, with simulated observations only:

```sh
python ai/examples/conversation.py
```

It shows greeting → category → budget → product question → reply plus cart read →
cart proposal → observed completion. It performs no search, browser action, audio
playback or external API call. Unit tests cover multi-turn slots, ordinal/pronoun
references, replay, owner separation, action progress and concurrent dialogues.


## Grounded product comparison

Dialogue now supports “compare”, “kaunsa sasta hai?”, “previous vs selected” and
explicit first/second/third pairs against saved products. With exactly two products,
a generic comparison can use both; larger ambiguous sets ask for an explicit pair.
Comparing never changes the selected product or dispatches a browser action. It
can answer from fresh evidence while another task is pending.

`ConversationReply.comparison` contains at most eight rows with separate left/right
`Fact` records and snapshot IDs. Price differences use Decimal only when both
prices and currencies match. Missing values stay unknown; differing currencies
produce no conversion, cheapest-product claim or calculated difference. Stale,
future, same-product and ambiguous snapshot observations are rejected.

Specification labels are matched conservatively by whitespace/case; values and
units are retained as observed. No inference that “30 hours” equals “1800 minutes”
or that a bigger number makes a better product is made. Duplicate ambiguous labels
are omitted. Long display text can be shortened with an explicit note; the full
source values remain in structured rows. Prices are observed listed prices, not
checkout totals, historical bargains, stock guarantees or overall quality scores.

The pure `compare_products(left, right, now=..., config=...)` function is also
available for a future adapter. It performs no model/API call, storage operation
or currency lookup. The caller must supply owned, normalized observations. Fresh
here means within the configured observation window, not independently rechecked
with the retailer.

`python ai/examples/conversation.py` includes an offline comparison after the
simulated cart flow. The fixtures are demonstration data, not real product offers.

## Cross-website cheapest-offer search

The main price-comparison use case is the **same exact product across multiple
stores**. Saved-product side-by-side comparison above remains a separate helper;
it does not perform this search. Explicit cross-website dialogue requests now ask
for brand/model, exact variant and delivery postcode, and disclose that the browser
adapter is not connected. They cannot silently return the two saved products.

`shopping_ai.market` supplies the provider-independent AI workflow:

1. Resolve a `ProductIdentity`: brand, exact model, condition and **all relevant
   variant attributes** (storage/RAM/colour/size/pack count/region as applicable).
   A normalizer/interpreter must ask when uncertain; never use title similarity
   as proof that two products match. This module does not infer identity from HTML.
2. The authenticated caller passes 2–6 trusted `Store` adapter configurations and
   the delivery context to `plan_search`. It returns bounded `SearchTask` records
   with encoded HTTPS search URLs and visible execution required. These are
   requests for the future extension adapter, not executed browser actions.
3. Persist the owned search checkpoint, then have the extension open/search the
   configured stores in visible Chrome tabs. The adapter must enforce its own
   allowlist on redirects, observe the exact listing/variant/seller and obtain
   costs for the supplied postcode. No CAPTCHA bypass or hidden fallback exists.
4. Normalize observed evidence into `SearchReceipt` / `Offer`. Only the trusted
   adapter may provide receipts; website text and model output are not trusted
   proof of identity, ownership or costs. The backend must validate the actual
   authenticated principal and bind adapter receipts to the issued task.
5. `accept_search_receipt` checks owner/session/search/task/version, delivery
   context, exact store origin, duplicate receipts and duplicate offer evidence.
   Persist its returned state with **database compare-and-swap** before using it.
   In-process version checks alone do not prevent concurrent database writes.
6. `rank_offers` includes fresh in-stock exact matches in the requested currency.
   Total = listed price + observed shipping + observed extra tax/fees. Use zero
   only when explicitly observed free/included; unknown costs stay `None`.
   Conditional bank/member/coupon discounts are retained as notes, never deducted.
7. Show structured ranked offers, seller, listing links, evidence timestamps and
   exclusions. `message` / `speech_text` support future UI/TTS integration. Equal
   cheapest totals retain all ties. While a store is pending/failed/blocked or a
   potentially competing offer has uncertain evidence, there is no confirmed
   cheapest claim. Complete results mean lowest observed total **among checked
   stores and returned offers**, never a guarantee across the entire internet.

`python ai/examples/market_search.py` runs a fully offline demonstration: the lower
listed price loses after shipping is included. No browser, network, LLM, database,
frontend or backend integration is included. Store extraction adapters, automatic
identity resolution and persisted execution scheduling remain integration work.
Do not expose the pure checkpoint helpers as unauthenticated APIs. Preserve the
state/receipts per user; there is no shared mutable registry in this module.

### Conversation adapter contract

After clarifying/confirming exact identity, call
`Conversation.begin_market_search(state, principal, identity=..., delivery_context=...,
stores=..., expected_version=state.version)`. The result contains the owned
checkpoint in `reply.state.active_market` and the initial tasks in
`reply.search_tasks`. Persist using conversation-version CAS **before** scheduling
those tasks. Re-delivering an old reply must not enqueue its tasks twice; the
adapter should use `(search_id, task_id)` as the durable dispatch key.

Feed each normalized browser receipt to `Conversation.accept_market_receipt` with
the current conversation version. Intermediate replies are `working`; after all
stores have replied, the conversation reply is `complete`, while
`reply.market_result.status` distinguishes complete evidence from partial coverage.
Progress/final replies never redispatch search tasks. The final structured result
contains ranked offers and listing links; message/speech text includes a bounded
price breakdown. A higher price before shipping can legitimately be the winner.

An unfinished search prevents a new search or cart/variant task from replacing it.
Product questions from saved evidence remain allowed. A `stop` turn clears the
active market checkpoint; its late receipts are rejected. The executor must also
cancel queued browser work when it receives stop—clearing an AI checkpoint alone
does not stop actions already running in Chrome. A replacement search has a new ID
and rejects receipts from earlier searches. Never derive principal IDs from the
user's spoken message. No STT/TTS client or live store adapter is implemented here.

### Collect and confirm exact search details

Configure `Conversation(..., stores=(...))` with trusted supported-store adapters.
A cross-store request starts an owned `MarketDraft`. Through normal `respond`
turns, the offline fallback asks for brand, exact model, all variant attributes,
condition (new/used/refurbished), and a six-digit Indian delivery postcode.
It accepts direct answers to the current question or explicit labelled updates:

```text
brand: Example; model: Phone One; attributes: storage=128GB,colour=black; condition: new; postcode: 201001
```

The completed draft is shown back to the user. Only a separate exact `confirm`,
`yes confirm` or `haan confirm` turn creates a search plan. Corrections update the
summary and require confirmation again. Early confirmation, ambiguous duplicate
labels, invalid postcodes or malformed variant details produce another question;
invalid input preserves the existing draft. `stop` clears the draft. No configured
stores means no search plan, even after confirmation. Search confirmation permits
only this read-only search, never a cart update or purchase.

This is deliberately a conservative **offline fallback**, not general free-form
product understanding. It does not extract “Samsung S24 128GB” into reliable model
and variant fields, infer which variant dimensions a catalogue requires, validate
that a model actually exists, or invent omitted details. The user must explicitly
supply and confirm all relevant distinctions; a future catalogue/NLU adapter can
resolve those before using `begin_market_search`. Attribute corrections replace
the entire attribute map, so the summary must include every desired distinction.
Current postcode collection is India-specific; generic `plan_search` still accepts
an adapter-supplied delivery context. `market_search.py` now demonstrates dialogue
collection → confirmation → simulated browser receipts → final ranked offers.

### Model-independent detail interpreter

Inject `detail_interpreter=` into `Conversation` to replace the conservative
collector through the async `DetailInterpreter.extract(message, draft)` protocol.
It returns a schema-validated **complete proposed `MarketDraft`**, not browser
commands. This is an integration interface; no paid API or production NLU adapter
is bundled. `OfflineDetailInterpreter` remains the default.

The first cross-store request is passed to this interpreter too, allowing an
injected adapter to extract several explicit details from one natural sentence.
Changed values must occur literally in the current user message (case and
whitespace normalization only). Invented model/variant/postcode values, clearing
known scalar fields, and silently dropping variant requirements are rejected.
Existing unchanged details may be retained. Replacing/removing attributes requires
explicit `attributes:` / `replace` / `reset` wording. The final summary still needs
a separate user confirmation; source occurrence alone does not establish product
existence, semantic correctness or user intent, and does not defeat every prompt
injection. The adapter must treat user text as data and preserve unknown fields.

Only a private draft copy is passed to the interpreter. Calls have bounded
concurrency, capacity wait and execution timeout using AIConfig limits; provider
failure text is never reflected to the user. Cancellation releases the capacity
permit and propagates. The limit applies per Conversation instance/process, so
reuse instances and add distributed admission control in a future multi-worker
backend. Interpreter implementations must be stateless across users or isolate
their own per-request context. No keys, principals or browser sessions are sent by
this interface.

After a market search, a generic “kaunsa sasta hai?” / “cheapest” follow-up reranks
its saved observations with the **current clock** and never redispatches search
tasks. Expired prices lose their cheapest claim. Explicit ordinal/previous product
references continue to use saved-product comparison instead.

## Live model connection (optional, provider-neutral)

Install `python -m pip install -e './ai[chat]'`. `ChatProvider` implements all three
protocols: `DecisionProvider.decide`, `Interpreter.interpret`, and
`DetailInterpreter.extract`. It speaks the Chat Completions JSON wire format;
compatible providers can be changed through trusted configuration without changing
conversation/orchestration code. This does not imply every provider/model supports
this format. No provider is selected automatically, and the old Gemini adapter
and default offline `create_ai()` behavior are unchanged.

Set these server environment variables explicitly (do not commit real keys):

```text
AI_CHAT_ENDPOINT=https://your-trusted-provider.example/v1/chat/completions
AI_CHAT_MODEL=your-available-model-id
AI_CHAT_API_KEY=your-server-key
AI_CHAT_RESPONSE_MODE=json_object
AI_CHAT_TOKEN_PARAMETER=max_completion_tokens
```

`AI_CHAT_API_KEY` is optional for endpoints that do not require bearer auth. Use
`max_tokens` instead of `max_completion_tokens` only if your chosen endpoint
requires that field. If it lacks JSON mode, explicitly set response mode `text`;
application JSON/schema validation still applies, but output reliability depends
on the model. There is no automatic downgrade, endpoint switch or retry. This
adapter requires HTTPS, including for any self-hosted/local service; no direct
plain-HTTP local endpoint support is provided. Endpoint configuration is trusted
server configuration, never supplied by a model, extension client or retailer.

```python
from shopping_ai import Conversation, Orchestrator, ShoppingAI
from shopping_ai.providers.chat import ChatProvider, ChatSettings

async with ChatProvider(ChatSettings.from_env()) as model:
    ai = ShoppingAI(model, model.config)
    dialogue = Conversation(
        Orchestrator(ai),
        interpreter=model,
        detail_interpreter=model,
        stores=trusted_store_configs,
    )
    # Use dialogue.start/respond/accept_market_receipt with authenticated
    # principals and persisted conversation-version CAS as documented above.
```

Keep this async context alive for the worker/application lifetime to reuse its
connection pool, then close it during shutdown. Do not reuse a client across
separate event loops. Transport concurrency applies across all three protocols
on this provider instance; multi-worker limits still need backend coordination.
An injected HTTP client belongs to its caller and must be closed by that caller.
The default owned client ignores environment proxies and never follows redirects.

For an **opt-in live text-only smoke test**, run `python ai/examples/live_chat.py`.
It may incur model-provider charges. It wires real intent and detail extraction
into the same conversation flow but supplies no store adapters or browser executor;
it cannot claim a website was searched. Type `exit` to finish. We have not run a
live model request during this implementation. Tests use HTTPX MockTransport and
verify real request/response construction, grounding and dialogue integration.

JSON mode guarantees neither our business rules nor exact schema conformance;
this adapter includes the schema in the system prompt, validates returned JSON
locally, then keeps the existing source-evidence checks and user confirmation.
Truncated/refused/tool-call outputs fail closed. Responses are capped at 64 KiB,
requests have capacity/deadline limits, and auth/rate-limit/network errors use
redacted stable codes. No native model tools execute. Relevant user text, draft
fields (including postcode), and allowlisted product observations are sent to the
configured model service; provider retention policies remain the deployer's choice.
No raw HTML, cookies, browser credentials or principal identifiers are added by
this adapter. API keys remain in server-side authorization headers.

Wire-format reference: [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
The implementation uses portable JSON-object/text responses with local validation,
not vendor-enforced strict JSON Schema or a vendor-specific SDK.

## Voice pipeline integration

`VoiceConversation` composes existing conversation/orchestration with replaceable
async `Transcriber` and `Synthesizer` protocols:

```text
bounded audio clip -> STT -> validated transcript -> Conversation.respond
-> owned conversation reply/state -> TTS -> bounded speech audio
```

This is an implemented pipeline, **not a bundled STT/TTS service**. Supply adapters
for your selected services or a local engine. No microphone capture, audio decoding,
playback, voice cloning, browser recording permissions, network endpoint or audio
storage is implemented here. Tests use explicitly fake speech adapters; no real
speech recognition, synthesis or end-to-end microphone demo has been verified.

```python
from shopping_ai import AudioClip, VoiceConversation, VoiceTurn

voice = VoiceConversation(dialogue, transcriber=stt_adapter, synthesizer=tts_adapter)
result = await voice.respond(
    state,
    authenticated_principal,
    VoiceTurn(request_id="unique-turn-id", audio=AudioClip(format="webm", data=recorded_bytes)),
    expected_version=state.version,
)
```

`VoiceTurn` shares `ConversationContext` with text turns, so a trusted caller may
also supply product snapshots, current browser target, available tools and an exact
mutation authorization. The STT adapter receives **only audio and language**, not
these permissions, principal IDs or browser credentials. The transcript becomes
the current user message; all existing source checks, explicit search confirmation
and mutation policy still apply. Never grant cart permission merely because an
STT/model output requests it.

Ownership, version, request replay, context and audio bounds are checked before
transcription. Input/output audio is limited to 5 MB and supported format labels
wav/webm/ogg/mp3; labels and byte limits do not prove valid or safe decoded media.
The speech adapter must validate content, duration/decoder limits and format support.
The AI library does not write raw recordings to disk, and audio bytes are excluded
from model serialization/repr. User transcripts remain normal conversation history;
apply retention/deletion rules in future persistence and speech-service adapters.

When STT provides confidence below the configurable threshold (default 0.75), no
conversation step or TTS is run; ask the user to repeat or type. Missing confidence
is treated as unavailable, not an automatic rejection or a guarantee of correctness.
Different recognizers have different confidence calibration. A stop button should
use the existing authenticated text cancellation path immediately rather than wait
for audio recognition. Voice “stop” also reaches that same path after transcription.

`VoiceReply.status=complete` means the audio pipeline produced a reply and speech;
inspect `reply.status` for the actual shopping task status. It does not mean an
item was added or websites were searched. Transcription/conversation failures return
no checkpoint. **Synthesis failure preserves `result.reply`**, including the updated
conversation state and any pending proposals. Persist it normally. Retry only
`voice.speak(result.reply, authenticated_principal)`; never replay the user turn to
retry speech. The same `speak` method can render receipts/progress replies from
`accept_result` and `accept_market_receipt` without repeating STT or planning.

Persist every returned checkpoint with backend owner checks and version CAS before
browser dispatch or playback. A repeated stale candidate must not redispatch tasks.
Discard returned audio if the user stopped or a newer conversation version has
superseded it. Browser action deadlines still apply after TTS latency. Where needed,
use text conversation and separate `speak` calls so state can be persisted before
synthesis. No speech result authorizes browser execution.

Both stages have configured timeout and bounded per-instance capacity/wait.
Async cancellation propagates and releases permits; the caller must cancel the
old request and stop playback for barge-in. Distributed scheduling, durable replay
protection and transport disconnect handling remain backend/extension work.

### Compatible live STT/TTS adapters

Install `python -m pip install -e './ai[chat,speech]'`. The optional `SpeechProvider`
implements both speech protocols against compatible HTTPS audio APIs: multipart
`POST /audio/transcriptions` and JSON `POST /audio/speech`. STT and TTS can have
separate endpoints, models and credentials. No vendor/model/voice is chosen for you.
Configure only trusted server endpoints:

```text
AI_STT_ENDPOINT=https://your-stt-service.example/v1/audio/transcriptions
AI_STT_MODEL=your-stt-model
AI_STT_API_KEY=your-stt-server-key
AI_TTS_ENDPOINT=https://your-tts-service.example/v1/audio/speech
AI_TTS_MODEL=your-tts-model
AI_TTS_API_KEY=your-tts-server-key
AI_TTS_VOICE=your-supported-voice
AI_TTS_FORMAT=wav
```

Keys are optional for services without bearer auth. `AI_TTS_FORMAT` supports wav
or mp3. Model/voice/language support depends on your configured service; these
adapters do not translate arbitrary vendor APIs. Credentials are not interchangeable
unless you deliberately configure them that way. No automatic .env loading occurs.

```python
from shopping_ai.providers.speech import SpeechProvider, SpeechSettings
from shopping_ai.voice import VoiceConversation

async with SpeechProvider(SpeechSettings.from_env()) as speech:
    voice = VoiceConversation(dialogue, transcriber=speech, synthesizer=speech)
    # Call voice.respond or voice.speak using owned checkpoints as above.
```

Keep this context alive for the application lifetime and close it on shutdown.
The adapter reuses a bounded connection pool, ignores environment proxies, refuses
redirects, and does not retry failed requests. Both request/response bodies are
bounded, and external errors become redacted stable codes. STT uploads the actual
bounded clip with a fixed filename/content type and requests JSON text. `en` maps
to English and `hi-Latn` maps to Hindi recognition; Hindi STT may output Devanagari.
The voice bridge normalizes only whole explicit Hindi stop/confirm utterances;
it does not transliterate product/model/variant data. Natural Hindi interpretation
requires the configured dialogue model. Latin-script Hindi pronunciation is not
independently verified and depends on the selected TTS model.

This plain STT response supplies no calibrated utterance confidence, so it returns
`confidence=None`. Do not assume the low-confidence gate can detect recognition
errors without a recognizer-specific confidence adapter. Missing or invalid text
fails closed. TTS accepts nonempty text up to 4,096 characters and returns bounded
mp3/wav audio; longer replies preserve the text checkpoint and report speech
failure, with no silent truncation or replay. The response MIME type and a small
format-header sanity check reject obvious non-audio output, **not** every invalid
codec payload. Decoding, duration/resource limits, playback validation and recording
permissions remain the extension/service adapter's responsibility.

Opt-in one-turn smoke test (may incur STT/model/TTS charges):

```sh
python ai/examples/live_voice.py /path/to/recording.webm --output /path/to/reply.wav
```

Also configure the `AI_CHAT_*` variables from the live text section. The output
extension must match `AI_TTS_FORMAT`; existing output files are never overwritten.
The script reads a bounded input and writes audio only when all stages succeed.
It supplies no live store/browser adapter or durable session, so it is a speech
integration smoke test, not a shopping completion test. Produced speech is
AI-generated; identify it as such in the eventual playback UI.

No live transcription/synthesis request has been run here. MockTransport tests
verify multipart payloads, separate credential routing, binary output, voice flow,
invalid responses, timeouts/capacity, size limits, redirects and failure handling.
Audio and reply text are sent to the configured services; their retention policies
are separate from this package's in-memory behavior.

API wire references: [transcriptions](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
and [speech](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create).

### Search deadlines and partial-result settlement

Every search and issued `SearchTask` now carries the same aware `deadline_at`.
`AIConfig.search_timeout_seconds` / `AI_SEARCH_TIMEOUT_SECONDS` controls the total
search window (default 120 seconds, allowed 1–600), separately from individual
model/STT/TTS request deadlines and evidence freshness. `plan_search` also accepts
an explicit `timeout_seconds` for standalone use. Enforce task deadlines before
browser dispatch; the deadline is not permission to keep a background tab running.

`accept_search_receipt` now **requires an explicit trusted `now`**. At or beyond
the search deadline, it rejects late receipts. Clock values before search creation
or naive timestamps are rejected. The caller must use its server clock, never a
model/client-provided time. Receipt status `timed_out` is reserved for the lifecycle
settler and cannot be submitted through the browser-receipt API.

At deadline, call `Conversation.expire_market_search(state, principal,
expected_version=state.version)` and persist its checkpoint with CAS. A future
backend scheduler should invoke it even while the user is idle. The method invokes
the pure `expire_search` helper: it retains returned evidence and creates a bounded
timed-out receipt for each unreturned store. Settlement is idempotent at the market
checkpoint level; it never reruns searches. Ownership and version checks apply to
settlement as well. A browser worker must separately cancel queued/in-flight work;
changing an AI checkpoint does not stop Chrome.

`respond` and `begin_market_search` also settle an overdue pending search before
handling new work, so users cannot get stuck behind an abandoned search forever.
Ranking an overdue checkpoint returns partial coverage immediately, even before
explicit settlement. Existing prices still have to pass their independent freshness
window. Missing/failed/blocked/timed-out stores are named in the result; no confirmed
cheapest claim is made with unresolved coverage. New searches receive new IDs and
reject old receipts. There are no automatic retries, timers, background jobs or
provider-switch fallbacks inside the AI package.

Checkpoint validation now rejects duplicate tasks/stores, mismatched deadlines,
unknown/duplicate receipt tasks and receipts bound to another search/owner/session/
delivery context. These are data-integrity checks, not authentication or a database
lock. Legacy demo checkpoints lacking `deadline_at` must be recreated; do not invent
a deadline on untrusted checkpoint data. The eventual persistence adapter should
version its stored checkpoint schema before migrating production sessions.

### Verified offer → visible navigation → fresh selected product

A user can request `open cheapest`, `cheapest offer kholo`, or `open second offer`.
The caller must attach a current visible `BrowserTarget` to the text/voice turn.
`Meaning.intent=open_offer` is only a routing proposal: the application also requires
an explicit supported English/Hinglish open command and refuses negated/combined
cart/purchase requests. The model never supplies a navigation URL.

`ConversationReply.status=ready_to_navigate` contains an `OfferNavigation` in
`reply.navigation` and in `state.pending_navigation`. It is a **proposal**, not a
successful page opening. The URL is exactly the fresh verified offer's URL from
search evidence. Generic cheapest-open requests require a unique confirmed winner;
ties, incomplete coverage or unknown costs ask for an explicit offer. Ordinal choices
bind to stored displayed offer IDs, so expired filtering cannot silently shift
“second offer” to a different listing. Expired selected evidence requires refresh.

Persist state with CAS, then have the future extension executor navigate the same
foreground user tab. Enforce its source tab/document binding, URL allowlist,
foreground visibility and deadline before executing; never open a hidden browser.
Navigation has its own short deadline, bounded by model-step timeout and the
remaining offer-freshness window. No new cart grant or mutation permission is issued.
Actual navigation/scrolling/typing remain extension work, not performed by this package.

After observing the actual page, send `NavigationReceipt` to
`Conversation.accept_navigation_receipt(state, principal, receipt,
expected_version=state.version)`. A successful receipt includes action/search/owner/
session binding, exact final URL, a visible target in the same tab, canonical observed
identity and a fresh `ProductSnapshot` with the same document/time. Wrong variant,
URL, tab, timestamps or expired receipts are rejected. Redirects are not silently
trusted; the current adapter requires the exact issued offer URL. Browser receipts
must come from the trusted normalizer/executor, never fabricated by an LLM.

Only verified receipt evidence selects the new product, after which existing product
questions/variant/cart policy can use its fresh snapshot. A changed/unknown listed
price, currency or stock invalidates the affected store's cached search receipt;
its old lowest-price claim cannot continue. Refresh the market search to restore
coverage. Failed navigation selects nothing. Stop clears pending navigation;
late/duplicate receipts cannot revive it. New user work settles an expired proposal
without automatically replaying it. Pending navigation blocks replacement search or
cart tasks but permits saved-evidence questions. Voice can speak both the proposal
and verified result using the same existing bridge.

## Single integration entry point

`shopping_ai.runtime.AssistantRuntime` routes all implemented AI events through the
same Conversation/VoiceConversation instance. It is an in-process application
boundary, **not an HTTP/WebSocket server, authentication layer or database**. No
frontend/backend files were changed. Existing lower-level APIs remain available.

| Event kind | Payload | Existing transition |
|---|---|---|
| `text` | `turn: ConversationTurn` | Interpret/respond; stop/cancel are normal text turns |
| `audio` | `turn: VoiceTurn` | STT → the same dialogue → TTS |
| `market_result` | `receipt: SearchReceipt` | Bind/store observations and rank offers |
| `navigation_result` | `receipt: NavigationReceipt` | Verify opened offer and select fresh product |
| `browser_result` | `result: BrowserResult` | Verify read/variant/cart evidence |
| `advance` | no extra fields | Plan the next step after an observation |
| `resume` | `target: BrowserTarget` | Explicitly resume a paused browser run |
| `expire` | `scope: browser` or `market` | Settle the corresponding deadline |

The discriminated `AssistantEvent` union rejects unsupported event kinds and extra
fields. `EVENTS.json_schema()` exposes the contract programmatically; no duplicate
hand-maintained JSON schema files are generated. Principal, saved state and expected
version are separate trusted arguments, never inferred from event text. Audio is
binary data supplied by the upload/stream adapter; it is not automatically base64
decoded from JSON. Audio output stays excluded from metadata serialization and
must be transported separately by the future backend.

```python
from shopping_ai import ConversationTurn
from shopping_ai.runtime import TextEvent, live_runtime
from shopping_ai.providers.chat import ChatSettings
from shopping_ai.providers.speech import SpeechSettings

async with live_runtime(
    ChatSettings.from_env(),
    speech=SpeechSettings.from_env(),
    stores=trusted_store_configs,
) as assistant:
    initial = await assistant.start(authenticated_principal)
    # Persist initial.state under its owner before handling subsequent turns.
    result = await assistant.handle(
        saved_state,
        authenticated_principal,
        TextEvent(turn=ConversationTurn(request_id="unique-id", message="search websites")),
        expected_version=saved_state.version,
        speak=True,
    )
    checkpoint = result.state
    reply = result.conversation_reply
    # Persist checkpoint using CAS before dispatching reply.action,
    # reply.search_tasks, reply.navigation, or playing speech.
```

`live_runtime` explicitly wires one ChatProvider into intent interpretation, detail
extraction and grounded decisions, plus an optional SpeechProvider into STT/TTS.
It owns their connection-pool lifecycle and closes them on shutdown or exception.
Keep one context per worker/event loop and separate saved checkpoints per user.
It does not start network calls during construction. `AI_PROVIDER` applies to the
legacy `create_ai` factory, not this explicit live wiring. To run fully offline,
construct `AssistantRuntime(existing_offline_conversation, optional_fake_voice)`.

Outputs have `kind=text` with ConversationReply or `kind=voice` with VoiceReply.
Python properties `result.conversation_reply` and `result.state` provide a common
view without duplicating checkpoints in serialized output. If STT fails before
conversation processing, both properties are None: keep the previous saved state.
If only TTS fails, the conversation checkpoint is present and must still be saved.
Use `assistant.voice.speak(reply, principal)` to retry speech only.

For non-audio events, `speak=True` also speaks receipt progress/final responses.
Voice must be configured before such work is attempted. Ownership/version errors
are raised before any provider work. Pending browser actions reject extra advance
events with `BROWSER_RESULT_REQUIRED`; wait for their receipt instead. This does
not replace durable worker deduplication: use action IDs or `(search_id, task_id)`
as dispatch keys, CAS state updates, and reject stale queued work in the executor.
The runtime does not dispatch anything automatically or persist state for you.

An offline integration test exercises detail collection → search receipts → chosen
offer → verified navigation receipt → cart baseline → proposed add → verified cart
quantity with spoken progress/final results. It tests **simulated observations and
fake speech**, not real ecommerce execution or live microphone/provider quality.

## Repeatable model readiness and quality evaluation

```sh
python -m shopping_ai.evaluate --check
python -m shopping_ai.evaluate --check --voice
python -m shopping_ai.evaluate
python -m shopping_ai.evaluate --live
```

`--check` validates required environment variable names, settings shape and optional
HTTP dependency availability without network traffic. `--voice` additionally checks
speech configuration. It never prints configured values or API keys. Exit 2 means
missing/invalid setup. `ready_to_attempt_live_test` means configuration looks usable,
not that credentials, model access, endpoints or speech quality have been verified.
API keys remain optional for compatible endpoints without bearer authentication.

The default command runs ten fixed English/Hinglish intent/detail cases using the
offline fallback. The current baseline is **9/10**: explicitly labelled details work,
while one natural-language complete-detail extraction case does not. This is a
known quality limitation, not a passing natural-language assistant benchmark.
The command intentionally exits 1 if any case fails; do not weaken expected results
to hide unsupported behavior. Passing unit tests and benchmark coverage are separate.

`--live` explicitly sends ten sequential model requests using configured ChatProvider
settings and may incur provider charges. It is not run automatically by tests or
imports. Reports include per-case IDs/outcomes and aggregate exact-match counts;
provider exception bodies/model-generated content are not logged. Detail proposals
also pass source-evidence validation. No live model evaluation was performed during
implementation because endpoint/model configuration is absent in this workspace.

These are a small reproducible smoke benchmark, not a statistically representative
quality measurement, security proof, latency/load benchmark, live ecommerce test or
speech evaluation. Run with the actual chosen model before claiming natural-language
capability, then expand cases from observed user failures. Real browser and microphone
acceptance testing still require their respective executor/UI integrations.

### Provider selection can stay undecided

`create_runtime` is the generic, non-networking assembly function. Unlike the
compatible-HTTP `live_runtime` convenience context, it accepts **independent
implementations** of every protocol and reads no endpoint/model environment values:

```python
from shopping_ai.runtime import create_runtime

assistant = create_runtime(
    decision_adapter,
    interpreter=intent_adapter,
    detail_interpreter=detail_adapter,
    transcriber=stt_adapter,
    synthesizer=tts_adapter,
    stores=trusted_store_configs,
)
```

One LLM adapter may implement all three model roles, or the roles may use different
models/services. STT and TTS need not use the same service or SDK. Omit both speech
adapters for text-only use; supplying only one is rejected. The generic factory
validates required callable roles, shares one conversation across text/audio and
receipts, and retains existing bounds/ownership/policy checks. It does not load keys,
select a vendor, open connections or close injected adapters. The caller owns their
lifecycle and must isolate adapter-internal state between users.

`live_runtime` now delegates to this same generic factory while retaining ownership
of the compatible HTTP providers it creates. The offline market example and live
text/voice smoke scripts also use the common runtime/event path rather than separate
manual wiring. Custom SDKs need only implement the protocols; their API does not
have to match Chat Completions or the bundled speech endpoints.

Run `python ai/examples/market_search.py` to exercise the shared integration without
choosing a vendor or setting any API credentials. Offers remain simulated; this
command does not search real stores. Provider selection is required only for live
quality/latency evaluation, not for continuing provider-independent integration.

## Versioned checkpoint save/restore (no database adapter)

The runtime now exposes a bounded, versioned JSON checkpoint codec:

```python
payload = assistant.snapshot(result.state, authenticated_principal)
# Future backend writes payload and state.version with owner-scoped SQL/CAS.
restored = assistant.restore(
    payload_from_trusted_store,
    authenticated_principal,
    conversation_id=owned_row_conversation_id,
    expected_version=owned_row_version,
)
```

Only ConversationState is serialized; provider clients, API keys, sockets and raw
audio are not part of it. The checkpoint **does contain conversation text, product
observations and any caller authorization grants**, so store it privately and apply
backend retention/access controls. It is ordinary JSON, not encrypted or signed.
Never treat a user-uploaded snapshot as trusted ownership or authorization evidence.
The backend must authenticate the principal and load the record through an
owner/session-scoped query before decoding it. The codec does not authenticate you.

Schema version 1 is explicit. Restore checks owner/session, conversation ID and
expected conversation version; rejects unknown schema versions, duplicate JSON
keys, invalid/non-finite JSON, extra fields and invalid nested contracts; and returns
only redacted CheckpointError codes on failure. Both input and output have a 4 MB
checkpoint budget. This can be narrower than theoretical maxima of every nested
contract combined; bound/trim observations before saving. If persistence fails,
retain the old committed state and do not dispatch the candidate's browser work.

Restore preserves request/action/search IDs, timestamps, deadlines, selected product
and bounded history. It does not make old observations fresh, renew grants, reset
versions or revive cancelled actions. Existing freshness/lifecycle checks still
apply. Unsupported schema versions require an explicit migration strategy, not a
silent upgrade based on caller-supplied data.

This is ready for a future Neon/PostgreSQL adapter but creates no SQL tables,
database connections, session registry or background writer. Durable CAS and atomic
outbox/task dispatch remain backend responsibilities. A decoded state passing its
metadata checks is **not** a distributed lock or anti-replay guarantee by itself.
The complete simulated runtime flow now saves/restores between events to verify
that search, navigation and cart checkpoints survive a JSON round-trip. A separate
test verifies restored navigation keeps its original expiry and still rejects late
receipts. No live database persistence has been tested.

## Separate display and spoken comparison replies

Cross-store ranking now accepts `language="en"` or `"hi-Latn"`; Conversation passes
its saved language for receipts, timeout settlement and price follow-ups. Detailed
`message` retains the price/delivery/fees breakdown. `speech_text` is a shorter,
deterministic summary derived from the **same calculated evidence**, not an extra
LLM response. The voice bridge already speaks speech_text, so this is integrated
without choosing a TTS provider or changing the frontend/backend.

Speech retains checked-store scope, missing coverage, conditional-discount limits,
lowest verified total and tied-offer count where applicable. Incomplete evidence
still produces no confirmed cheapest claim. Repeated store IDs are deduplicated
for presentation while every tied offer/link remains in the structured ranking.
A maximum-size 120-tied-offer test verifies bounded message/speech length and no
loss of ranked offers. Speech summaries avoid reading full listing URLs; UI links
remain available from the structured result.

Saved-product pair comparisons similarly have short speech summaries covering
listed-price difference/equality/missing/different-currency evidence, unverified
delivery/fees and absence of an overall quality recommendation. Display rows and
fact IDs are unchanged. This prevents long comparison tables from being sent
wholesale to the speech service. Other unusually long replies can still exceed a
specific TTS service's input limit and preserve their text checkpoint on failure;
no content is silently truncated or user turn replayed.

Tests verify the generated summary is exactly what reaches the speech adapter.
This verifies deterministic text integration, not actual pronunciation or audio
quality; those remain dependent on the eventual selected service.

## Offer evidence integrity and exclusion explanations

A newly submitted offer must have been observed after search creation and no later
than the trusted receipt-processing clock. Future or pre-search timestamps reject
the receipt without advancing state or consuming its task; a corrected receipt may
then be submitted. Do not send retailer publication dates or model-generated times
as `observed_at`; the trusted browser normalizer records when it actually observed
the page. This prevents a future-dated observation from simply aging into eligibility.
Existing stale evidence remains excluded when ranking with the current clock.

`MarketResult.exclusions` pairs each excluded offer ID/store with explicit reasons:
identity/variant mismatch, currency mismatch, out of stock, unknown stock, future
observation, pre-search observation, stale evidence, or unknown total cost. Multiple
reasons can apply. Existing `excluded_offer_ids` remain available and must match
this structured list. Detailed display text points to the reasons without changing
spoken cheapest/partial-coverage claims. Exclusion never invents a replacement
price, shipping charge, stock status or exchange rate.

Owned search checkpoints now also reject duplicate offer/snapshot IDs and links
outside the issuing store's exact HTTPS origin during revalidation/restoration.
Normalizers must assign globally distinct evidence IDs across stores within a
search; raw retailer IDs without store/source namespacing are insufficient.
Derived ranking contracts check that totals equal observed price + shipping + fees,
that ranked/excluded IDs do not overlap, and that a complete result retains every
minimum-price tie. Partial results still cannot contain confirmed cheapest offers.

These checks enforce consistency of supplied observations; they do not establish
that retailer claims are independently true or authenticate the submitting client.
Browser receipts still require a trusted, owner-bound executor/normalizer. No live
store extraction or provider selection was added in this change.
