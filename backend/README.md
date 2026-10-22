# AI Shopping Copilot - Backend (Phase 1 & Phase 2)

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


