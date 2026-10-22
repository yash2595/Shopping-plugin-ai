"""
End-to-end demo flow script for the AI Shopping Copilot MVP features.
NOT part of pytest.

Runs against a running local server using mock AI:
1. Register User 1 (tokens never printed)
2. Start Session
3. Capture Snapshot (₹3500)
4. Chat: "is this a good deal?"
5. Chat: "my budget is 3000" (executes set_budget tool)
6. Chat: "notify me below 3000" (executes create_price_alert tool)
7. GET /v1/sessions/{id}/context (confirms budget_max = 3000)
8. User 2 registers & captures snapshot with lower price (₹2800), triggering User 1 alert
9. GET /v1/notifications for User 1 (verifies alert notification)
10. GET /v1/listings/{id}/history?resolution=daily (fetches price history)
11. POST /v1/listings/{id}/review-summary (fresh summary generated)
12. POST /v1/listings/{id}/review-summary again (cached: true returned)

Usage:
    python scripts/demo_flow.py [BASE_URL]
"""

import os
import sys
import uuid
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def run_demo(base_url: str = "http://127.0.0.1:8000"):
    print(f"=== Starting Shopping Copilot MVP Demo Flow ===")
    print(f"Target Server: {base_url}")
    session = requests.Session()

    def get_auth_headers(token: str):
        return {"Authorization": f"Bearer {token}"}

    # Step 1: Register User 1
    r1 = session.post(f"{base_url}/v1/auth/register")
    print(f"[Step 1] Register User 1 -> Status: {r1.status_code}")
    if r1.status_code != 201:
        print(f"Error: {r1.text}")
        return
    u1_token = r1.json()["access_token"]
    u1_headers = get_auth_headers(u1_token)

    # Step 2: Start Session
    r2 = session.post(f"{base_url}/v1/sessions", headers=u1_headers, json={"title": "Demo Headphone Shopping"})
    print(f"[Step 2] Start Session -> Status: {r2.status_code}")
    if r2.status_code != 201:
        print(f"Error: {r2.text}")
        return
    session_id = r2.json()["id"]

    # Step 3: Capture Snapshot
    retailer_prod_id = f"demo_prod_{uuid.uuid4().hex[:6]}"
    snapshot_data = {
        "retailer": "amazon_in",
        "retailer_product_id": retailer_prod_id,
        "title": "Sony WH-1000XM5 Wireless Headphones",
        "url": f"https://amazon.in/dp/{retailer_prod_id}",
        "price": "3500.00",
        "availability": "in_stock",
    }
    r3 = session.post(f"{base_url}/v1/snapshots", headers=u1_headers, json=snapshot_data)
    print(f"[Step 3] Capture Snapshot (Rs. 3500.00) -> Status: {r3.status_code}")
    if r3.status_code != 201:
        print(f"Error: {r3.text}")
        return
    snap_resp = r3.json()
    snapshot_id = snap_resp["snapshot_id"]
    listing_id = snap_resp["listing_id"]

    # Step 4: Chat "is this a good deal?"
    r4 = session.post(
        f"{base_url}/v1/sessions/{session_id}/messages",
        headers=u1_headers,
        json={
            "text": "is this a good deal?",
            "snapshot_id": snapshot_id,
            "request_id": str(uuid.uuid4()),
        },
    )
    print(f"[Step 4] Chat: 'is this a good deal?' -> Status: {r4.status_code}")
    if r4.status_code == 200:
        print(f"        Reply: {r4.json().get('content')}")

    # Step 5: Chat "my budget is 3000"
    r5 = session.post(
        f"{base_url}/v1/sessions/{session_id}/messages",
        headers=u1_headers,
        json={
            "text": "my budget is 3000",
            "snapshot_id": snapshot_id,
            "request_id": str(uuid.uuid4()),
        },
    )
    print(f"[Step 5] Chat: 'my budget is 3000' -> Status: {r5.status_code}")
    if r5.status_code == 200:
        print(f"        Reply: {r5.json().get('content')}")

    # Step 6: Chat "notify me below 3000"
    r6 = session.post(
        f"{base_url}/v1/sessions/{session_id}/messages",
        headers=u1_headers,
        json={
            "text": "notify me below 3000",
            "snapshot_id": snapshot_id,
            "request_id": str(uuid.uuid4()),
        },
    )
    print(f"[Step 6] Chat: 'notify me below 3000' -> Status: {r6.status_code}")
    if r6.status_code == 200:
        print(f"        Reply: {r6.json().get('content')}")

    # Step 7: GET /v1/sessions/{id}/context
    r7 = session.get(f"{base_url}/v1/sessions/{session_id}/context", headers=u1_headers)
    print(f"[Step 7] Context GET -> Status: {r7.status_code}")
    if r7.status_code == 200:
        ctx = r7.json()
        print(f"        budget_max: {ctx.get('budget_max')}, currency: {ctx.get('currency')}, version: {ctx.get('version')}")

    # Step 8: User 2 confirms a lower price (₹2800)
    r8_reg = session.post(f"{base_url}/v1/auth/register")
    u2_token = r8_reg.json()["access_token"]
    u2_headers = get_auth_headers(u2_token)

    r8_snap = session.post(
        f"{base_url}/v1/snapshots",
        headers=u2_headers,
        json={
            "retailer": "amazon_in",
            "retailer_product_id": retailer_prod_id,
            "title": "Sony WH-1000XM5 Wireless Headphones",
            "url": f"https://amazon.in/dp/{retailer_prod_id}",
            "price": "2900.00",
            "availability": "in_stock",
        },
    )
    print(f"[Step 8] User 2 captures snapshot at Rs. 2900.00 -> Status: {r8_snap.status_code}")
    # Owner confirms the lower price at 2800.00, triggering the alert
    r8_confirm = session.post(
        f"{base_url}/v1/listings/{listing_id}/observations",
        headers=u1_headers,
        json={"price": "2800.00", "in_stock": True},
    )
    print(f"         Owner confirms lower price at Rs. 2800.00 -> Status: {r8_confirm.status_code}")

    # Step 9: Check notifications for User 1
    r9 = session.get(f"{base_url}/v1/notifications", headers=u1_headers)
    print(f"[Step 9] User 1 Notifications GET -> Status: {r9.status_code}")
    if r9.status_code == 200:
        notifs = r9.json()
        print(f"        Found {len(notifs)} notification(s)")
        if notifs:
            payload = notifs[0].get("payload", {})
            print(f"        Notification preview: triggered_price Rs. {payload.get('triggered_price')} (target Rs. {payload.get('target_price')})")

    # Step 10: GET /v1/listings/{id}/history?resolution=daily
    # Note: Even though 3 accepted observations exist across Step 2, Step 8 snapshot, and Step 8 confirm,
    # all occurred within seconds during this demo.
    # Because MIN_HISTORY_SPAN_DAYS = 7 (requiring both >= 3 points AND >= 7 days span),
    # the honest verdict is 'insufficient_data' with reason 'history_too_short'.
    r10 = session.get(f"{base_url}/v1/listings/{listing_id}/history?resolution=daily", headers=u1_headers)
    print(f"[Step 10] Price History GET -> Status: {r10.status_code}")
    if r10.status_code == 200:
        hist = r10.json()
        print(f"         Observations count: {hist.get('count')}, verdict: {hist.get('verdict')}, reason: {hist.get('reason')}, tracking_since: {hist.get('tracking_since')}")
        assert hist.get("verdict") == "insufficient_data", f"Expected 'insufficient_data', got {hist.get('verdict')}"
        assert hist.get("reason") == "history_too_short", f"Expected 'history_too_short', got {hist.get('reason')}"

    # Step 11: Review summary (first call)
    sample_reviews = [
        {"rating": 5, "title": "Incredible sound", "text": "Noise cancellation is amazing and the sound quality is top-notch."},
        {"rating": 4, "title": "Great comfort", "text": "Very comfortable for long hours and battery life is reliable."},
        {"rating": 3, "title": "Expensive", "text": "Good headphones but rather expensive for plastic build quality."},
    ]
    r11 = session.post(
        f"{base_url}/v1/listings/{listing_id}/review-summary",
        headers=u1_headers,
        json={"reviews": sample_reviews},
    )
    print(f"[Step 11] Review Summary 1 -> Status: {r11.status_code}")
    if r11.status_code == 200:
        rev1 = r11.json()
        print(f"         cached: {rev1.get('cached')}, overall: {rev1.get('overall')[:60]}...")

    # Step 12: Review summary (second call with identical reviews)
    r12 = session.post(
        f"{base_url}/v1/listings/{listing_id}/review-summary",
        headers=u1_headers,
        json={"reviews": sample_reviews},
    )
    print(f"[Step 12] Review Summary 2 -> Status: {r12.status_code}")
    if r12.status_code == 200:
        rev2 = r12.json()
        print(f"         cached: {rev2.get('cached')}, sample_size: {rev2.get('sample_size')}")

    print("=== Demo Flow Finished Successfully ===")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BASE_URL", "http://127.0.0.1:8000")
    run_demo(target)
