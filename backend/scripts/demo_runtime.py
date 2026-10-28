"""Loopback-only HTTP demonstration with synthetic browser observations.

Run the configured mock API first. This does NOT launch a browser or shop online.
"""

import argparse
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

ORIGIN = "https://demo.shop.test"
TARGET = {"tab_id": 7, "document_id": "synthetic-document", "active": True, "window_focused": True}


def now():
    return datetime.now(timezone.utc).isoformat()


def run(base_url):
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use a plain loopback-only HTTP backend URL")
    with httpx.Client(base_url=base_url, timeout=120, trust_env=False) as client:

        def call(path, data=None):
            response = client.post(path, json=data) if data is not None else client.get(path)
            if response.status_code >= 400:
                try:
                    code = response.json()["error"]["code"]
                except (ValueError, KeyError, TypeError):
                    code = "HTTP_ERROR"
                raise RuntimeError(f"Demo stopped: HTTP {response.status_code} {code}")
            return response.json()

        registration = call("/v1/auth/register", {})
        client.headers["Authorization"] = "Bearer " + registration["access_token"]
        try:
            session = call("/v1/sessions", {"title": "Synthetic AI HTTP demo"})
            reply = call(f"/v1/ai/sessions/{session['id']}/conversation", {"language": "en"})
            base = f"/v1/ai/conversations/{reply['conversation_id']}"

            def post(suffix, data):
                nonlocal reply
                result = call(
                    base + suffix,
                    {"event_id": uuid.uuid4().hex, "expected_version": reply["version"], **data},
                )
                if "message" in result:
                    reply = result
                    print(f"v{reply['version']} {reply['status']}: {reply['message']}")
                return result

            def claim():
                dispatch_id = reply["work_ids"][0]
                work = call(
                    base + f"/work/{dispatch_id}/claim",
                    {
                        "claim_id": "journal-" + dispatch_id,
                        "expected_version": reply["version"],
                        "target": TARGET,
                        "origin": ORIGIN,
                    },
                )
                print("SIMULATED executor received:", work["work"]["tool"]["name"])
                return work

            def receipt(work, observation):
                return post(
                    f"/work/{work['dispatch_id']}/receipts",
                    {
                        "claim_token": work["claim_token"],
                        "origin": ORIGIN,
                        "browser": {"observed_at": now(), "target": TARGET, **observation},
                    },
                )

            print("OFFLINE PROVIDER + SYNTHETIC BROWSER RECEIPTS; no retailer action is executed.")
            post(
                "/events",
                {
                    "kind": "text",
                    "message": "What is the price?",
                    "target": TARGET,
                    "origin": ORIGIN,
                },
            )
            work = claim()
            product = {
                "snapshot_id": "synthetic-snapshot",
                "product_id": "synthetic-phone",
                "document_id": TARGET["document_id"],
                "observed_at": now(),
                "title": "Synthetic Phone",
                "variants": [{"variant_id": "black", "label": "Black"}],
                "selected_variant_id": "black",
                "price": "100.10",
                "currency": "INR",
                "in_stock": True,
            }
            receipt(work, {"outcome": "observed", "product": product})
            post("/events", {"kind": "advance"})
            post(
                "/confirmations",
                {
                    "confirmed": True,
                    "operation": "add_to_cart",
                    "snapshot_id": product["snapshot_id"],
                    "product_id": product["product_id"],
                    "variant_id": "black",
                    "quantity": 1,
                    "mode": "add_quantity",
                    "max_unit_price": "100.10",
                    "currency": "INR",
                    "target": TARGET,
                    "origin": ORIGIN,
                },
            )
            work = claim()
            cart = {"document_id": TARGET["document_id"], "observed_at": now(), "lines": []}
            receipt(work, {"outcome": "observed", "cart": cart, "cart_coverage": "complete"})
            post("/events", {"kind": "advance"})
            work = claim()
            receipt(work, {"outcome": "unknown", "error_code": "SYNTHETIC_LOST_RECEIPT"})
            post("/events", {"kind": "resume", "target": TARGET, "origin": ORIGIN})
            post("/events", {"kind": "advance"})
            recovery = claim()
            if recovery["work"]["tool"]["name"] != "read_cart":
                raise RuntimeError("Recovery must never repeat a cart mutation")
            cart = {
                "document_id": TARGET["document_id"],
                "observed_at": now(),
                "lines": [
                    {"product_id": product["product_id"], "variant_id": "black", "quantity": 1}
                ],
            }
            receipt(recovery, {"outcome": "observed", "cart": cart, "cart_coverage": "complete"})
            if reply["status"] != "complete":
                raise RuntimeError("Synthetic recovery did not complete")
            print(
                "PASS: grounded answer, explicit confirmation, committed work and read-only recovery over HTTP."
            )
        finally:
            client.post("/v1/auth/logout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8019")
    run(parser.parse_args().base_url)
