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
Neon, real browser execution and voice are not connected. Review summaries,
comparison and free-form conversational answers remain later AI milestones.
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
not performed. Search/discovery, comparison, review synthesis and arbitrary
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
