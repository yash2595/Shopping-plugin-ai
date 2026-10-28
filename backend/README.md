# AI Shopping Copilot - Backend and integrated runtime

Backend service for the **AI Shopping Copilot** Chrome extension. Ingests real-time product snapshots, tracks append-only price observations with deduplication and sudden jump protection, computes real price history statistics, fires atomic price drop alerts, and manages idempotent conversational chat sessions.

---

## Tech Stack
- **Python**: 3.11+ / 3.12
- **Framework**: FastAPI (plain sync `def` routes)
- **Validation**: Pydantic v2 & `pydantic-settings` (strict `extra='forbid'`)
- **Database ORM**: SQLAlchemy 2.x (sync, typed `Mapped[]` style) + Psycopg 3
- **Migrations**: Alembic (all schema management; `Base.metadata.create_all` is never used)
- **Database Engine**: PostgreSQL (Neon-ready with PgBouncer connection pooling `prepare_threshold=None`)
- **Testing**: pytest + httpx/TestClient against a real PostgreSQL test database (`*_test`)

---

## Architecture & Dependency Rules

Strict modular monolith layout enforcing one-way dependency flow:
```
routers  -->  services  -->  repositories  -->  models
   |             |
   v             v
schemas         ai (contracts & mock - NEVER imports db/models)
```

- **`app/routers/`**: HTTP transport only. Validates request, delegates to services, returns typed schemas. Never touches the DB directly.
- **`app/services/`**: Core business domain logic (normalization, deduplication, price jump analysis, atomic alert triggers, idempotency, auth hashing, maintenance cleanup).
- **`app/repositories/`**: All database queries and persistence logic using SQLAlchemy 2.0 select statements.
- **`app/models/`**: Declarative models with typed annotations, UTC timestamptz, and UUID primary keys. Money is `NUMERIC(12,2)`.
- **`app/ai/`**: Decoupled AI provider interface (`contracts.py`) and deterministic `MockProvider` (`mock.py`).

---

## Setup & Running Locally

### 1. Prerequisites & Virtual Environment
```bash
cd backend
python -m venv .venv

# Windows Powershell
.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -e .[dev]
```

For core checkpoint integration and its PostgreSQL tests, install both local
packages from the repository root (the core remains independent of FastAPI):

```bash
python -m pip install -e './ai[dev]' -e './backend[dev]'
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and configure your database URLs and maintenance secret:
```env
ENV=development
DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/shopping
DIRECT_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/shopping
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/shopping_test
JOB_SECRET=dev-job-secret-token
```

### 4. Run Migrations
Apply all schema changes:
```bash
alembic upgrade head
```

### 5. Start Development Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Interactive docs are available at `http://localhost:8000/docs`.

---

## Running Tests

All integration tests execute against the dedicated test database specified in `TEST_DATABASE_URL`. The database name **must** end in `_test`, or the runner will refuse execution. Every test executes inside a savepoint transaction and is rolled back upon completion (with real commit concurrency test isolated and cleaned up).

```bash
pytest -v
```

---

## API Endpoints Overview

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `GET` | `/health` | Process liveness check | No |
| `GET` | `/health/db` | Database connectivity verification (`SELECT 1`) | No |
| `POST` | `/v1/auth/register` | Issues anonymous user opaque bearer token (stored as SHA-256) | No |
| `POST` | `/v1/auth/logout` | Revokes the current bearer token; returns 204 | Bearer Token |
| `POST` | `/v1/sessions` | Creates chat session scoped to current user | Bearer Token |
| `GET` | `/v1/sessions/{id}` | Retrieves session details and message history (returns 404 for other users) | Bearer Token |
| `POST` | `/v1/sessions/{id}/messages` | Post chat message; idempotent replay with `Idempotent-Replay: true` | Bearer Token |
| `POST` | `/v1/snapshots` | Ingests product snapshot, dedups 6h price, flags >50% jumps | Bearer Token |
| `POST` | `/v1/listings/{listing_id}/observations` | Records observation, evaluates active alerts atomically in same transaction | Bearer Token |
| `GET` | `/v1/listings/{id}/history?days=30` | Returns price history stats & observations; honest verdict requires >=3 points AND >=7 days span | Bearer Token |
| `POST` | `/v1/alerts` | Sets target price alert (409 ALERT_ALREADY_EXISTS if duplicate active alert) | Bearer Token |
| `GET` | `/v1/alerts` | Lists user alerts (optional `?status=`) | Bearer Token |
| `PATCH` | `/v1/alerts/{id}` | Updates target price or cancels alert (optimistic locking via `version`) | Bearer Token |
| `POST` | `/v1/alerts/{id}/rearm` | Rearms triggered alert, increments `arm_count`, sets status active | Bearer Token |
| `GET` | `/v1/notifications` | Lists notifications for user (`?status=pending`) | Bearer Token |
| `POST` | `/v1/notifications/{id}/ack` | Acknowledges alert delivery (`status='delivered'`) | Bearer Token |
| `POST` | `/internal/jobs/run` | Maintenance job (expires old tokens, purges snapshots, messages, notifications) | `X-Job-Secret` |

---

## Key Engineering Rules Implemented
1. **Security & Privacy**: No retailer passwords, cookies, or OTPs stored. Full tokens are never logged. Auth tokens are stored strictly as SHA-256 hashes. Internal maintenance job verified with constant-time comparison.
2. **User Data Isolation**: Every session, alert, and snapshot query filters explicitly by `user_id` extracted from the bearer token. Another user's resource returns `404 Not Found`.
3. **Atomic Alert Trigger**: Triggering updates status to `triggered` in a single atomic SQL statement with `RETURNING *`. Duplicate triggers are prevented and pending/rejected observations never fire alerts.
4. **Optimistic Locking**: Alerts use an explicit `version` column for updates, returning `409 Conflict` on stale writes.
5. **Data Integrity & Concurrency**: Money is `NUMERIC(12,2)`. Race conditions between concurrent qualifying observations produce exactly one notification.
6. **Honest Price Verdict**: A verdict other than `"insufficient_data"` is returned only if BOTH hold: at least 3 accepted observations AND history span is >= 7 days (`MIN_HISTORY_SPAN_DAYS = 7`). Otherwise returns `"insufficient_data"` with `reason` (`"not_enough_observations"` or `"history_too_short"`) and `tracking_since`.

---

## Feature to Endpoint Map

A guide for extension client integration (for Riya), detailing each of the six MVP features, calling order, and example JSON payloads.

---

### Feature 1: Page Snapshots
Ingests product metadata and prices extracted by the extension on shopping pages.

**Flow & Order:**
1. Call `POST /v1/snapshots` whenever a supported product page loads.
2. If polling or recording periodic price updates, call `POST /v1/listings/{listing_id}/observations`.

**Request (`POST /v1/snapshots`):**
```json
{
  "retailer": "amazon_in",
  "retailer_product_id": "B09XS7JWHH",
  "title": "Sony WH-1000XM5 Wireless Noise Cancelling Headphones",
  "url": "https://www.amazon.in/dp/B09XS7JWHH",
  "price": "29990.00",
  "availability": "in_stock"
}
```

**Response (`201 Created`):**
```json
{
  "snapshot_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "listing_id": "4a2c5f10-1842-43e9-a2e6-7b2432924401",
  "price_recorded": true,
  "price_summary": {
    "verdict": "insufficient_data",
    "reason": "not_enough_observations",
    "tracking_since": "2026-10-22T02:00:00Z",
    "lowest_price": null,
    "highest_price": null,
    "average_price": null,
    "observation_count": 1
  }
}
```

---

### Feature 2: Conversational Chat
Chat with AI assistant about the viewed product, deal evaluation, or alert creation.

**Flow & Order:**
1. Call `POST /v1/sessions` to initialize a session (or reuse an existing session).
2. Call `POST /v1/sessions/{id}/messages` with `snapshot_id` and unique `request_id`.
3. To reload past dialogue, call `GET /v1/sessions/{id}`.

**Request (`POST /v1/sessions/{id}/messages`):**
```json
{
  "text": "Is this a good deal?",
  "snapshot_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "request_id": "client-req-001"
}
```

**Response (`200 OK`):**
```json
{
  "id": "e8a946b5-0c74-4b53-8d69-3ebf3ff7a718",
  "session_id": "84c8be79-052a-4ee1-b956-6f7776b71bf3",
  "role": "assistant",
  "content": "The current price is ₹29990.00. There is no price history yet to confirm if this is a good deal.",
  "kind": "answer",
  "payload": null,
  "created_at": "2026-10-22T02:00:00Z"
}
```

---

### Feature 3: Structured Shopping Context
Maintains budget, shopping preferences (e.g., color, brand), and rejected products.

**Flow & Order:**
1. Session creation automatically creates a context. Optionally pass `carry_context_from: <previous_session_id>` in `POST /v1/sessions`.
2. Inspect context via `GET /v1/sessions/{id}/context`.
3. Update budget or merge preferences with `PATCH /v1/sessions/{id}/context` using optimistic locking (`version`).
4. Reject disliked products using `POST /v1/sessions/{id}/context/rejections`.
5. Remove rejections using `DELETE /v1/sessions/{id}/context/rejections/{listing_id}`.

**Request (`PATCH /v1/sessions/{id}/context`):**
```json
{
  "budget_max": "25000.00",
  "preferences": {
    "color": "black",
    "brand": "sony",
    "delivery_speed": null
  },
  "version": 1
}
```

**Response (`200 OK`):**
```json
{
  "id": "c1f7f0e6-5991-49fa-9860-23a544c20793",
  "session_id": "84c8be79-052a-4ee1-b956-6f7776b71bf3",
  "budget_max": "25000.00",
  "currency": "INR",
  "preferences": {
    "color": "black",
    "brand": "sony"
  },
  "version": 2,
  "created_at": "2026-10-22T02:00:00Z",
  "updated_at": "2026-10-22T02:01:00Z",
  "rejections": []
}
```

---

### Feature 4: Review Summary
Summarizes page reviews into pros, cons, and themes without storing raw review text.

**Flow & Order:**
1. Extract reviews currently visible on the page (minimum 3 usable reviews, maximum 30).
2. Call `POST /v1/listings/{id}/review-summary`.
3. To view existing summaries without re-synthesizing, call `GET /v1/listings/{id}/review-summary`.

**Request (`POST /v1/listings/{id}/review-summary`):**
```json
{
  "reviews": [
    {
      "rating": 5,
      "title": "Superb sound",
      "text": "Exceptional active noise cancellation and crystal clear sound quality."
    },
    {
      "rating": 4,
      "title": "Good battery life",
      "text": "Comfortable cushions and long battery life. Slightly warm in summer."
    },
    {
      "rating": 2,
      "title": "Expensive",
      "text": "Not worth the premium price tag given the mostly plastic body."
    }
  ]
}
```

**Response (`200 OK`):**
```json
{
  "listing_id": "4a2c5f10-1842-43e9-a2e6-7b2432924401",
  "overall": "Based on 3 reviews, customer feedback is mostly positive with an average rating of 3.7/5.",
  "pros": [
    "Solid sound quality and build",
    "Long battery performance"
  ],
  "cons": [
    "Limited reported drawbacks in sample"
  ],
  "themes": [
    "Sound Performance",
    "Battery Life",
    "Value for Money"
  ],
  "caveat": "Based on 3 reviews shown on the page, not all reviews.",
  "sample_size": 3,
  "average_rating_in_sample": "3.67",
  "cached": false,
  "kind": "summary",
  "created_at": "2026-10-22T02:02:00Z"
}
```

---

### Feature 5: Price History
Retrieves historical price trend points and valuation statistics.

**Flow & Order:**
1. Call `GET /v1/listings/{id}/history?resolution=daily&days=30` to display lowest price trend per day.

**Response (`200 OK`):**
```json
{
  "listing_id": "4a2c5f10-1842-43e9-a2e6-7b2432924401",
  "days": 30,
  "observations": [
    {
      "price": "29990.00",
      "in_stock": true,
      "captured_at": "2026-10-01T10:00:00Z"
    },
    {
      "price": "26990.00",
      "in_stock": true,
      "captured_at": "2026-10-15T14:30:00Z"
    },
    {
      "price": "24990.00",
      "in_stock": true,
      "captured_at": "2026-10-22T08:15:00Z"
    }
  ],
  "count": 5,
  "min": "24990.00",
  "max": "29990.00",
  "average": "27323.33",
  "first_seen": "2026-10-01T10:00:00Z",
  "verdict": "good_deal",
  "reason": null,
  "tracking_since": "2026-10-01T10:00:00Z"
}
```

---

### Feature 6: Price Alerts & Notifications
Notify the user when price drops below a threshold.

**Flow & Order:**
1. Call `POST /v1/alerts` to create an active price alert.
2. Extension periodically polls `GET /v1/notifications?status=pending`.
3. When notification is displayed, call `POST /v1/notifications/{id}/ack` to mark delivered.
4. To modify or cancel an alert, call `PATCH /v1/alerts/{id}`.
5. To rearm a triggered alert, call `POST /v1/alerts/{id}/rearm`.

**Request (`POST /v1/alerts`):**
```json
{
  "listing_id": "4a2c5f10-1842-43e9-a2e6-7b2432924401",
  "target_price": "26000.00"
}
```

**Response (`201 Created`):**
```json
{
  "id": "f5e937d1-e60d-407b-a320-b08be26c11b1",
  "user_id": "1d8bf62d-0dbf-4e08-9df5-d4193b2a5ef0",
  "listing_id": "4a2c5f10-1842-43e9-a2e6-7b2432924401",
  "target_price": "26000.00",
  "status": "active",
  "arm_count": 0,
  "triggered_at": null,
  "triggered_price": null,
  "version": 1,
  "created_at": "2026-10-22T02:00:00Z",
  "updated_at": "2026-10-22T02:00:00Z"
}
```

---

## OpenAPI Specification
The single canonical OpenAPI 3.1 specification for all endpoints and schemas is maintained at:
- `backend/contracts/openapi.json`

## AI continuation: security and durable state

Reviewed base: `23bb0ad` on `main`. The October 19 handoff describes the earlier
`d96401f` core-only tree; the backend and Electron frontend were added afterwards.
This continuation extends the existing API rather than introducing another server.

### Legacy chat admission and authentication

- Chat validates the authenticated owner's session before looking up a replay.
  Request IDs retain their existing per-user uniqueness. Reuse with another session,
  changed text or changed snapshot returns `409 IDEMPOTENCY_CONFLICT`.
- A unique database admission record is committed before the provider runs. Provider
  waits hold no application DB transaction. Identical concurrent requests return
  `409 REQUEST_IN_PROGRESS`; a completed retry returns its exact stored reply ID,
  including after an application restart. Reply selection never relies on timestamps.
- Tool effects and their final reply association commit together. A failed final
  transaction leaves no budget/preference/alert effect. Its admitted request stays
  unfinished and must not be executed again automatically. A process crash after
  admission can leave `REQUEST_IN_PROGRESS` requiring operational reconciliation;
  there is no processing-lease recovery worker yet. Never advise blind retries with
  a new request ID for a potentially executed operation.
- This ledger is stored in existing private user-message payloads. Pre-upgrade
  messages without an exact request hash/reply binding return a conflict rather
  than guessing a historical reply. Deduplication lasts only while those retained
  messages exist (default 30 days); it is not a permanent mutation ledger.
- Model proposals independently require a matching explicit current command and
  exact amount/value. The conservative supported forms include `my budget is 3000`,
  `set my budget to 3000`, `notify me below 3000`, `I prefer black`,
  `prefer brand sony`, and `not this one, too expensive`. Negations, quoted commands,
  explanations and unmatched values request clarification. This is not a general
  Hinglish intent-quality claim. No cart or checkout tool is enabled in this API.
- JSON bodies are bounded to 262,144 bytes before route parsing, including chunked
  requests. Duplicate keys, depth above 32, non-finite numbers, invalid Unicode and
  NUL characters are rejected. Audio uploads use the separate bounded media path
  described below. Chat text is bounded to 2,000 characters.
- Public validation errors, database-health failures and unhandled-error logs do not
  echo private inputs/provider/database exception text. Unhandled errors have an
  `X-Request-ID` for correlation. `/v1/` responses use `Cache-Control: no-store`.
- Logout revokes the presented bearer token in the database. Invalid, expired and
  revoked tokens cannot replay saved replies. Anonymous demo auth is the default;
  verified API JWT authentication is available as described below. `ENV=production` disables registration
  unless `ALLOW_ANONYMOUS_REGISTRATION=true` is deliberately configured. Anonymous
  identities can bypass per-user budgets by re-registering; production still needs
  gateway admission controls and a real account/session lifecycle.
- Production refuses the sample maintenance secret or a secret shorter than 32
  characters. All deployment secrets must stay outside source and client output.

### Provider handling

One configured provider client is created/closed by each app's lifespan. Legacy
chat model calls use a bounded process-local executor (`AI_MAX_CONCURRENT_CALLS=8`,
`AI_TIMEOUT_SECONDS=5` by default). A timed-out HTTP request returns its safe fallback
without waiting for the provider thread. That thread retains its capacity slot until
it exits; its late result cannot mutate state. Gemini also receives a transport timeout.
The bounded executor and per-user quota are not a cross-worker/global cost limit.

### Private core checkpoint repository

Migration `b6e4d72f0a91` follows `234eb99aacb2`. It adds `ai_conversations` and an
owned-session composite key. `app/repositories/ai_checkpoint_repo.py` depends on the
locally installed `shopping_ai` package and exposes trusted create/load/CAS methods:

- Ownership is constrained both in SQL queries and by a composite foreign key to
  `sessions(user_id, id)`. A caller cannot bind one user's checkpoint to another's session.
- Exact codec UTF-8 JSON is stored as `TEXT`; restoration checks row version,
  conversation ID, owner/session and schema version. Money/IDs/deadlines are not rebuilt.
- Conditional `UPDATE ... WHERE version = expected RETURNING ...` accepts one writer
  at a time. Actual runtime version jumps are preserved. Equal-version writes only
  accept identical checkpoint bytes; changed bytes cannot bypass concurrency checks.
- Methods flush but never commit. The trusted application service must commit or
  roll back the whole state/event/outbox transaction before returning any work.
  The repository creates no HTTP endpoint and dispatches no browser actions.

PostgreSQL tests cover two independent racing connections, tenant/session isolation,
database rejection of a mismatched owned session, exact codec roundtrips, version
metadata corruption, same-version changes and caller rollback. Core-checkpoint tests
are explicitly skipped when the optional local core package is not installed; CI
installs both packages and exercises them. CI also migrates a fresh PostgreSQL 17
database, runs backend tests, and downgrades/upgrades this migration. Downgrade drops
private checkpoints; export needed state before using it outside disposable tests.

The implementation follows PostgreSQL's [conditional updates](https://www.postgresql.org/docs/current/sql-update.html)
and SQLAlchemy's [transactional DML](https://docs.sqlalchemy.org/en/20/core/dml.html).
Provider timeout handling accounts for Python's [executor shutdown behavior](https://docs.python.org/3/library/concurrent.futures.html).

## Local AI runtime

The runtime integration is now implemented in the existing FastAPI application.
It preserves `shopping_ai` as the source of business policy and state transitions;
there is no second agent loop/server and no model-generated browser code.
The October 18 architecture PDFs describe the older proposed WXT frontend. The
current repository already contains Electron, so this continuation adds its AI API
client contract without replacing the browser team's interface or executor.

### Start locally without model credentials

Install both packages from the repository root:

```sh
python -m pip install -e './ai[dev,chat,speech]' -e './backend[dev]'
```

Configure `backend/.env` using the existing example, with a local PostgreSQL database
in `DATABASE_URL` and a direct migration connection in `DIRECT_DATABASE_URL`.
Use a distinct disposable `*_test` database for tests. No paid provider is needed:
`AI_RUNTIME_ENABLED=true`, `AI_RUNTIME_PROVIDER=mock`, and blank speech endpoints
select offline text decisions with no external model/audio calls. The optional
feature defaults off when no `.env` is used, preserving standalone backend imports
without the local core package. The checked-in example enables it explicitly.

From `backend/`:

```sh
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8019
```

The API reference is at `http://127.0.0.1:8019/docs`. For the included synthetic HTTP
historical cart/recovery demonstration only, explicitly set
`AI_ANALYSIS_ONLY=false`, `AI_MUTATIONS_ENABLED=true`, `AI_RUNTIME_PROVIDER=mock` and keep
`AI_BROWSER_ORIGINS=["https://demo.shop.test"]` in that server's local environment.
In a second terminal, from `backend/`:

```sh
python scripts/demo_runtime.py --base-url http://127.0.0.1:8019
```

It registers a disposable anonymous demo identity, reads a synthetic product,
explicitly confirms a quantity operation, submits a complete cart baseline,
simulates a lost mutation result and recovers with `read_cart`. It prints each
committed version and never prints a bearer token. It accepts loopback servers only
and does not execute a retailer action. Its synthetic account/session is subject to
the existing retention job. Disable mutations again before connecting a real page
unless its packaged adapter and user confirmation UI have passed acceptance.

### Public API for the browser team

All routes below require the existing bearer token. Owner and session binding are
derived from authenticated storage; checkpoints, principals, provider settings,
capability grants and arbitrary scripts are never accepted in request bodies.

| Method | Route under `/v1/ai` | Behavior |
| --- | --- | --- |
| POST | `/sessions/{session_id}/conversation` | Create or restore one owned core conversation per existing session. |
| GET | `/conversations/{id}` | Safe current reply, version, observed products/facts and committed work IDs. |
| POST | `/conversations/{id}/events` | Typed `text`, `cancel`, `resume` or one bounded `advance` step. |
| POST | `/conversations/{id}/confirmations` | Explicit reviewed product/snapshot/variant/quantity/mode/ceiling/target confirmation. |
| GET | `/conversations/{id}/work` | Read-only pending/claimed metadata; no browser instruction payload. |
| POST | `/conversations/{id}/work/{dispatch_id}/claim` | Claim committed unexpired work in the owned session's execution lane. |
| POST | `/conversations/{id}/work/{dispatch_id}/receipts` | Submit a claim-bound typed browser/search/navigation observation. |
| POST | `/conversations/{id}/audio` | Bounded raw WAV/WebM/OGG/MP3 plus event/version headers; deferred TTS. |
| POST | `/conversations/{id}/speech` | Synthesize only the current committed saved reply; private version-bound bytes. |
| DELETE | `/conversations/{id}` | Delete owned checkpoint, events and work after in-flight/uncertain cart reconciliation. |

The canonical OpenAPI includes these optional-feature routes. Legacy chat, history,
review and alert routes remain backward compatible. The updated frontend
`ShoppingApiClient` exposes `startAiConversation`, `sendAiEvent`, `confirmAiAction`,
`getAiWork`, `claimAiWork`, `submitAiReceipt`, `sendAiAudio`, `synthesizeAiSpeech`
and `deleteAiConversation`. These are transport methods, not a DOM executor.
Its logout method now revokes the server token before clearing the local vault;
an unsupported-site 403 does not discard an otherwise valid identity.

Example initial text event after creating the conversation:

```json
{
  "event_id": "unique-user-turn-1",
  "expected_version": 1,
  "kind": "text",
  "message": "What is the price?",
  "target": {"tab_id": 7, "document_id": "current-document", "active": true, "window_focused": true},
  "origin": "https://demo.shop.test"
}
```

### Persistence, leases and browser recovery

Migration `c7f5a83e1b02` follows the owned checkpoint migration and adds
`ai_events` and `ai_dispatches`. Composite foreign keys bind every dependent row to
the same owned session/conversation. Each event has a canonical input hash, an
expected conversation version, a bounded processing lease and a random fencing token.
Identical completed retries return the exact saved public reply. Changed payloads
conflict. A retry can reclaim an expired **processing** lease only while its input
version is still current; the old producer cannot commit after its fence is replaced.
Provider computation can occur twice after a lease loss, but only one candidate
state and its work can commit. No external mutation occurs in provider processing.

After provider processing outside the DB transaction, the service locks the owned
execution lane and event, checks its live fence, compares/swaps the checkpoint and
writes public reply, browser work, receipt acknowledgment and event result in one
transaction. Failure rolls back candidate state and outbox together. Cancellation
bypasses provider admission and can overtake an in-flight model call; stale results
are discarded. This ledger is separate from the earlier legacy-message ledger.

Work IDs are stable. Claim IDs must be saved in the browser's durable local action
journal **before execution**. Retrying that exact claim returns the same fence/work;
a different claim cannot take it over. Check current claim/target/deadline immediately
before execution, and never execute a completed journal entry again. A claim timeout
does not prove a click failed and never authorizes a fresh cart click. One active
claim per owned session serializes visible work. The actual browser must enforce
foreground execution, packaged adapter permissions, native tab/document lifetime
and store origin. A correctly shaped receipt proves consistency, not DOM authenticity.

Browser receipts omit run/session/source bindings: the server fills those from its
owned dispatch. Product/cart observation times and target are validated by the core.
For opened navigation, use the same capture timestamp for the receipt and its
product snapshot; mismatched evidence returns a typed conflict without changing state.
Cart observations require explicit complete coverage; partial/unknown absence is
never interpreted as zero. Cancellation suppresses future claims but permits a late
already-claimed receipt to be recorded without reviving the run. Cart outcomes are
verified/failed/unknown; unknown operations resume through read-only reconciliation.
Removing configured mutation capability blocks pending mutation claims as well.

Run deadline settlement separately from the API, using the same database/config:

```sh
python -m app.ai.worker --once
python -m app.ai.worker --interval 2 --limit 20
```

Due work is durable across restarts. Workers use short owned-session locks with
`SKIP LOCKED`, recheck work identity, expire only the current matching operation and
commit settlement atomically. Parallel workers settle each item once. Expired cart
actions pause for uncertainty recovery; neither workers nor claim leases reissue
them. A due navigation is cancelled only when that exact proposal remains current.

### Voice, model configuration and quotas

Audio requests use raw binary `Content-Type: audio/wav|audio/webm|audio/ogg|audio/mpeg`,
`X-Event-ID` and `X-Expected-Version`. Optional `tab_id`, `document_id` and `origin`
query parameters bind a current foreground context. Uploads are bounded to 5,000,000
bytes before parsing, with a basic container-header check; full codec/duration
decoding is delegated to the configured speech service. Raw audio is never stored
in checkpoints, event payloads or logs. STT failure/low confidence leaves state
unchanged. Text remains usable with no speech provider.

Speech requests contain only `expected_version`. They call TTS on the saved text,
never STT/intent/actions. TTS failure leaves committed text/work intact. The server
checks version again after synthesis; stale bytes are discarded. Clients must also
stop playback on cancel/new input/logout and compare `X-Conversation-Version` before
playing. Daily speech admission is bounded separately; read-only uncertain-cart
recovery and cancellation remain available after the ordinary AI budget is exhausted.

The local default interpreter is deliberately conservative, not a real model or a
general Hinglish classifier. The original offline intent/detail quality benchmark
still scores 9/10: its `natural-details` case requires a richer detail interpreter;
the benchmark and that failing case have not been removed or relabelled. The nine
integration scenarios are a separate check. For a configured compatible provider, set
`AI_RUNTIME_PROVIDER=chat` plus server-only `AI_CHAT_ENDPOINT`, `AI_CHAT_MODEL` and
`AI_CHAT_API_KEY`. Decision, intent and detail adapters share one owned async client.
Set both `AI_STT_*` and `AI_TTS_*`, `AI_TTS_VOICE` and optional `AI_TTS_FORMAT` for
speech. Empty settings make no speech network calls; partial/invalid configuration
fails startup safely. Existing `AI_PROVIDER=mock|gemini` controls the legacy chat
route, not this new provider-neutral runtime. No vendor or paid plan is implied.

Configure explicit `CORS_ALLOWED_ORIGINS` for the actual UI. JSON and auth controls
still apply; no wildcard or `null` origin is required. CORS exposes the version/replay
headers needed by the typed client and is not a substitute for authentication.
Anonymous registration remains a local demo mechanism and is disabled in production
without explicit opt-in; a selected verified account provider, HTTPS deployment,
gateway abuse controls and real retailer acceptance are live release gates.

### Verified account authentication

`AUTH_MODE=anonymous` keeps the local demo token flow. For an identity provider
that issues signed **API access tokens**, set `AUTH_MODE=oidc`, `AUTH_ISSUER` (exact
HTTPS issuer), `AUTH_AUDIENCE` (this API's dedicated audience), and
`AUTH_JWKS_JSON` (a JSON JWKS object containing public verification keys). Obtain
these through the provider's trusted administration/configuration channel.
Never configure a client secret or private signing key here. This backend validates
bearer access tokens; it does not host a password database or an OAuth login UI.
An ID token whose audience is the browser application's client ID is not an API
access token and must not be used as one.

Every configured key needs a unique `kid`, `alg=RS256` with RSA >=2048 bits or
`alg=ES256` with P-256, and signing/verification usage. Startup rejects invalid,
private, weak or duplicate keys. Verification requires valid signature, exact
issuer, API audience, subject, expiry and issued-at; future not-before/issued-at
claims fail. Tokens and claims are bounded. Token-controlled key URLs and critical
extensions are rejected; verification makes no network request. This uses PyJWT's
asymmetric verification API with algorithms fixed by the configured key, following
its [algorithm-selection guidance](https://pyjwt.readthedocs.io/en/stable/api.html#jwt.decode).

For rotation, configure current and next **public** keys, restart every API process,
then retire the old key and restart again after the overlap. Changes take effect
on restart; there is no automatic JWKS fetch/refresh. Choose short access-token
lifetimes at the provider. Provider-side session revocation is bounded by token
expiry or removal of the signing key; this backend's logout immediately revokes
the specific presented token in PostgreSQL across API processes. It does not revoke
the provider's refresh token/session. Frontend sign-out must also use the provider's
own logout flow where required.

Migration head `d8a6b94f2c13` follows `c7f5a83e1b02`, adding an issuer/subject to
user mapping. Email/name claims never merge accounts. Concurrent first login
creates one owner without orphan users. The OIDC mode disables anonymous
registration in every environment, and opaque demo tokens are not accepted.
All existing ownership, event, confirmation, quota and replay controls still apply.
The typed frontend client accepts the selected login integration's secure
`tokenProvider`; use `allowAnonymousRegistration: false` to require sign-in.
Selecting/configuring the actual identity provider and its login/refresh flow is
still required before a live release.

### Readiness and capability discovery

`GET /health` is process liveness. `GET /health/ready` returns 200 only when the
database responds and its Alembic head matches the shipped migrations; otherwise it
returns a generic 503. It performs no model/speech request. This endpoint does not
certify upstream availability or worker health. Run the deadline worker alongside
the API, as below. `GET /health/db` remains a connectivity-only check.

Authenticated `GET /v1/ai/capabilities` reports configured provider/authentication
mode, mutation enablement, browser origins, speech configuration and request/quota
limits. It reveals no provider keys, internal grants or checkpoints. Configuration
flags describe setup, not evidence of a successful live provider call.
The client exposes `getAiCapabilities()` for integration.

### Local container stack

From the repository root with Docker Engine/Compose running, use a fresh random
URL-safe database password. PowerShell example:

```powershell
$env:SHOPPING_DB_PASSWORD = python -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up --build -d
```

Keep that password available for subsequent Compose commands. The stack runs
PostgreSQL 17 on a private container network, a one-shot migration job, the API
on `127.0.0.1:8000`, and a separate deadline worker. API/worker wait for successful
migration; database storage uses the named `shopping_data` volume. The Python
services run as a non-root user with a read-only filesystem, temporary `/tmp`,
dropped Linux capabilities and no new privileges. The build context excludes
environment files, Git data and frontend build/dependency trees.

This Compose file is deliberately a **local mock demo**: development environment,
anonymous auth, no speech/model network calls, no enabled store mutations. It does
not load backend `.env` or activate a paid provider. Use the native configuration
instructions above when preparing a separately reviewed live deployment.
Stop with `docker compose down`; the database volume is retained. Never remove the
volume to resolve a routine application error. `SHOPPING_DB_PASSWORD` is required
for configuration and must match the existing volume after its first initialization.

On this machine `docker compose config --quiet` passed, but Docker Engine was not
running, so image build/container startup have not been locally verified. The CI
container job builds and smoke-checks this stack when the workflow is run.

### Latest visual architecture phases

The newer six-page Final Visual Technical Architecture Report changes the product
target to a local-first **analytical browsing assistant**. It blocks cart/purchase
triggers and prefers browser-native speech and local inference. Its "Implemented"
and "Completed" labels were checked against this repository; WXT/MV3, offscreen
voice, safe DOM skills and end-to-end local routing were not already complete.
The old cart protocol is retained only for explicit compatibility/regression work.
Checkout/payment tools are not present.

1. **Implemented locally in this phase:** loopback Ollama inference for decisions,
   intent and detail extraction; deterministic degradation by default; explicit
   opt-in cloud fallback; a typed fallback indicator; analysis-only mutation gates;
   and successful-settlement worker heartbeat/Compose health checks.
2. **Implemented evidence analysis:** a stateless shopping endpoint, exact spec and
   review references, observed-price/stock/exact-spec shortlists, comparison rows,
   bounded source excerpts and deterministic coverage. Legacy unchecked answer prose
   is discarded; owner-scoped cached review summaries contain sample statistics
   only, without an LLM call. Abandoned legacy admissions
   retire with `REQUEST_INTERRUPTED`; late producers are fenced before SQL tools.
3. **Implemented desktop integration:** typed question input, current-page capture,
   up to four ephemeral product captures, explicit budget/RAM/storage/colour memory,
   API answers/source cards/comparisons, Stop/Clear, exact-text native highlight and
   review scrolling, sender checks and release-time sandboxing. Browser STT has an
   editable transcript and typed fallback; read-aloud selects an available local
   voice. Rating-filter clicking and actual-device acceptance remain pending.
4. **Remaining extension-local deployment:** WXT/MV3 architecture migration,
   optional-backend operation, offscreen/VAD lifecycle, independent real-retailer
   extraction acceptance, browser voice quality/permissions and complete account
   deletion. The desktop flow uses the existing backend; it is not an MV3 package.
5. **Acceptance:** representative real pages, source changes, stopped local server,
   offline operation, model/speech quality, two-user checks and measured costs.

The backend remains optional infrastructure for the eventual extension, but this
existing FastAPI deployment still uses PostgreSQL and persists conversation state.
Selecting local inference does **not** switch it to memory-only storage or implement
the report's extension-only privacy/lifecycle. These are explicit remaining gates.

#### Shopping analysis endpoint and desktop flow

Authenticated `POST /v1/ai/analysis` accepts a question, zero to four normalized
`AnalysisProduct` captures, a selected product reference, optional budget/currency/
exact specs and language. Each
capture binds a `ProductSnapshot` to a captured HTTPS source and up to 30 review
observations. The endpoint checks configured source origins, timestamp freshness,
input bounds and strict contracts. Exact lookup needs no model request. If a single
selected product has no exact match, an explicitly configured local/chat provider
may select canonical fact IDs under the daily AI quota. The server checks those
IDs, values and freshness again and renders captured facts itself; model prose and
actions are never forwarded. Provider waiting holds no database transaction. Page
text/history is not persisted; authentication and optional quota metadata use PostgreSQL.

Its `local_extractive` result contains source/snapshot/document/timestamp references,
exact observed values, comparison rows with unknown cells, an observed-price
shortlist and exact review excerpts. RAM/storage formatting is normalized without
unit conversion; unknown stock, wrong currency, conflicting specs and stale prices
exclude a recommendation. Generic "best" and unsupported use-case suitability ask
for criteria. Reviewer badges are returned as **observed page labels**, never as
independent proof of purchase, authenticity or customer consensus. Quote substrings
are checked again against current DOM text when the user clicks Show on page.

The current desktop UI invokes this endpoint, captures supported Amazon.in/Flipkart
pages, retains at most four products and explicit requirements in memory, and clears
them on Clear/app exit. It does not silently upload full HTML, cookies, payment
fields or unrelated page text. Native document counters change on full/SPA
navigation; capture/results/actions are rejected or discarded after a target change.
Only packaged scroll/highlight code runs, with exact visible-text checks. Cart and
purchase commands are absent from the analysis skill schema. Model dialogue still
uses the separate optional local-first runtime; this deterministic path does not
claim general language-model reasoning or global product search.

The packaged `file://` UI uses a trusted-main-frame JSON HTTP bridge rather than
allowing wildcard/null CORS. `SHOPPING_BACKEND_URL` in the Electron main-process
environment chooses one trusted backend origin (default `http://localhost:8000`);
non-loopback backends require HTTPS. Only `/v1/` JSON requests go there, redirects
are refused, request/response sizes are bounded and bearer auth comes from the
memory vault. Retailer cookies are never forwarded. Browser-development requests
continue using normal fetch and explicit CORS. Binary server speech is separate;
the desktop analysis flow uses browser speech/local voices.

Legacy `/messages` answers render owned observed numeric/history facts, never
unchecked provider text. Review statistics do not charge AI quota or call a model;
their caveat is server-written. Pre-grounding/ownerless cache rows are not served.
Statistics are owner-scoped in both cache fingerprints and latest-summary lookups;
capturing the same listing does not reveal another user's sample. Expired entries
refresh by atomic upsert, preserving the unique key. Exact review quotes use the
stateless endpoint and are not saved in those cache rows. Internal cache ownership
metadata is excluded from public DTOs and model context.

#### Local Ollama route

Use an already installed Ollama service and a downloaded model. No model is pulled
or installed by this application. Configure:

```dotenv
AI_RUNTIME_ENABLED=true
AI_RUNTIME_PROVIDER=local
AI_ANALYSIS_ONLY=true
AI_LOCAL_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions
AI_LOCAL_MODEL=llama3.2
AI_LOCAL_TIMEOUT_SECONDS=5
AI_LOCAL_FALLBACK=deterministic
AI_MUTATIONS_ENABLED=false
```

This uses Ollama's documented [OpenAI-compatible local chat endpoint](https://docs.ollama.com/api/openai-compatibility#local-server-usage),
without an authorization header. HTTP is permitted only for this explicit local
transport: loopback host, port 11434, exact `/v1/chat/completions` path; arbitrary
hosts, ports, credentials, query strings and redirects are rejected. Remote chat
endpoints continue requiring HTTPS. Model/context size limits, JSON parsing and
policy/grounding remain in application code. The loopback address is the API
machine's loopback, not a remote user's device. In a container it refers to that
container; the checked-in Compose stack deliberately remains a mock demo.

If local inference cannot connect, times out or produces invalid output, the
default route uses existing deterministic intent/detail rules and fact selection.
It sends no fallback request to a cloud endpoint. Unsupported language still
clarifies; the known natural-detail benchmark limitation is not hidden. Cancellation
does not start a fallback call. `inference_fallback=deterministic|cloud|null` is
persisted in each public reply so the future UI can show a degradation badge;
replay preserves the saved indicator and a later healthy turn clears it.

Cloud fallback is separate, deliberate configuration: set
`AI_LOCAL_FALLBACK=cloud` plus valid `AI_CHAT_ENDPOINT`, `AI_CHAT_MODEL` and any
required server-side key. This permits forwarding filtered request/evidence data
after a local inference failure. No provider is guaranteed free or unlimited.
Without that opt-in, even configured cloud settings are not used as fallback.
A loopback destination alone cannot establish whether the local service itself
uses cloud models; run a downloaded local model with that service's cloud features
disabled for an on-device deployment. This phase used mocked transports; actual
Ollama/model installation and quality have not been accepted on this machine.

`AI_ANALYSIS_ONLY=true` is the default and overrides `AI_MUTATIONS_ENABLED`: both
confirmation and dispatch claim reject variant/cart mutations. Read/reconciliation
and Stop remain available for historical in-flight work. Native extension-level
cart blocking is also required and is not replaced by these server gates.

#### Worker progress health

The prior hosted container job failed because `compose up --wait` included a worker
without a healthcheck. The worker now supports `--heartbeat-file`, updated only
after a successful settlement pass. Compose checks the heartbeat is under 30 seconds
old; database/settlement failures do not refresh it. This measures worker progress,
not retailer connectivity or model quality. API readiness remains separate.
Local heartbeat tests and Compose validation can run without Docker Engine; a new
hosted/container startup check is still needed before declaring that gate green.

The preceding local-provider phase passed 598 core tests, 162 backend tests against PostgreSQL 17 and 20
client tests. Client/TypeScript and Electron bundles passed. New tests cover local
connection refusal, timeout and invalid JSON; exact-fact deterministic fallback;
no implicit cloud request even with cloud settings present; explicit opt-in cloud
routing; cancellation without fallback; event-scoped degradation/replay; both
analysis mutation gates; and success-only worker heartbeat refresh. These use
synthetic/mocked transports, not an installed Ollama model or real retailer.

### Verification and remaining ownership boundary

Before this phase, local Python 3.12 checks passed: 590 core tests, 150 backend
tests against disposable PostgreSQL 17, all nine offline core scenarios and the real
loopback HTTP synthetic cart/recovery demo. The frontend has 20 passing tests and
passes TypeScript/Vite and Electron bundling. Core Ruff and changed-file static
checks pass. The backend reports an upstream Starlette/httpx TestClient deprecation
warning; frontend lint has three pre-existing warnings in untouched UI/native files.

The new API tests include independent-connection CAS/lease races, cancellation
during provider waits, no held provider DB transactions, duplicate claims/receipts,
outbox rollback, parallel deadline workers, cross-store destination binding,
speech-only retry, stale playback and cross-user/revoked-token access. CI now runs
the backend/runtime and client gates alongside the original Python matrix.
The workflow configuration was checked locally; a hosted GitHub CI run has not
been verified for this integration.

The server and typed client are local implementations; live Neon/model/speech
credentials, actual visible retailer adapters, native document identity, browser
journal/playback/UI wiring and measured live quality remain the browser/deployment
acceptance boundary. No live retailer, Neon or paid model was contacted in this run.
Checkout/payment is outside the exposed tool set. This does not claim universal
retailer coverage or production capacity.



### Integrated analytical shopping flow verification

The current local integration passed 621 core tests, 172 backend tests against
disposable PostgreSQL 17 and, after conversation/navigation integration, 43 client
tests (836 total). TypeScript/Vite build,
Electron bundle and native TypeScript check passed. Core Ruff and Compose config
validation passed. Client lint has three existing warnings; backend tests report
one upstream TestClient deprecation. The packaged extractor, exact-evidence
highlighter and trusted HTTP bridge have executable fixture tests. A loopback
HTTP smoke used synthetic captures; no retailer or paid provider was contacted.

This verifies the analytical desktop integration, not real retailer compatibility
or production acceptance. A real Electron hidden-window fixture smoke now verifies
avatar activation, dialogue, exact backend answers, Clear and shared budget memory.
The hidden window correctly refuses foreground-required navigation; successful
navigation is exercised by native fixture tests. Live ten-page retailer checks,
focused visible-window navigation, device microphone/local voices, Ollama answer quality,
MV3/WXT packaging and offscreen voice remain pending. Analysis-only mode keeps
cart/purchase disabled.
