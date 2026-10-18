# Standalone shopping AI — v0.1

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

This is one decision step, not a persistent autonomous agent. Review summaries,
comparison, free-form conversational answers, action receipts/reconciliation, auth,
Neon persistence, browser execution and voice are later integration milestones.
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
  engine.py         One step, timeout, evidence validation and response rendering
  cli.py            JSON fixture runner
examples/           Price/colour, cart proposal, missing product
 tests/             Contract, policy, freshness, errors and mocked SDK tests
```

Context is capped in **characters**, not tokens. Old history is removed first;
relevant facts/task constraints are never silently truncated. Oversized remaining
context is blocked. Default limits: 24,000 context characters, 1,000 output tokens,
30-second call timeout, snapshot age up to 120 seconds, and 8 caller-counted steps.
No repair loop or provider retries are enabled in v0.1. Limits are engineering
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

Next: connect a real retailer's read-only product snapshot to this interface;
then add a backend-owned task/action store and a verified browser executor. Add
free-form, evidence-backed summaries only with separate claim evaluation.

Official references: [SDK](https://googleapis.github.io/python-genai/),
[structured output](https://ai.google.dev/gemini-api/docs/structured-output).
