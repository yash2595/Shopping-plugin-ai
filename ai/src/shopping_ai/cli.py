"""Run one decision against a JSON fixture without starting a backend."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from shopping_ai import AIRequest, ShoppingAI, create_ai
from shopping_ai.page_evidence import (
    MAX_CAPTURE_BYTES,
    decode_captured_page,
    extract_product_snapshot,
)
from shopping_ai.providers import MockProvider


async def _capture_demo(source: Path, product_id: str, message: str):
    with source.open("rb") as handle:
        page = decode_captured_page(handle.read(MAX_CAPTURE_BYTES + 1))
    now = datetime.now(UTC)
    snapshot = extract_product_snapshot(
        page, snapshot_id=uuid4().hex, product_id=product_id, now=now
    )
    response = await ShoppingAI(MockProvider(), clock=lambda: now).decide(
        AIRequest(
            request_id=uuid4().hex,
            message=message,
            language="en",
            product=snapshot,
            available_tools=[],
        )
    )
    return {
        "mode": "offline-capture",
        "product": snapshot.model_dump(mode="json"),
        "answer": response.model_dump(mode="json"),
    }, response.status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, nargs="?", help="Path to an AIRequest JSON file")
    parser.add_argument(
        "--capture", type=Path, help="Normalize CapturedPage JSON and answer offline"
    )
    parser.add_argument("--product-id", help="Trusted stable product ID; required with --capture")
    parser.add_argument(
        "--message", help="Read-only offline capture question; default price and stock"
    )
    parser.add_argument(
        "--fresh-demo",
        action="store_true",
        help="Refresh fixture timestamps for OFFLINE mock examples only",
    )
    args = parser.parse_args()
    if bool(args.request) == bool(args.capture):
        parser.error("Provide an AIRequest file or --capture, exclusively")
    if args.capture and (not args.product_id or args.fresh_demo):
        parser.error("--capture requires --product-id and cannot refresh capture timestamps")
    if args.request and (args.product_id is not None or args.message is not None):
        parser.error("--product-id and --message apply only to --capture")
    try:
        if args.capture:
            result, status = asyncio.run(
                _capture_demo(args.capture, args.product_id, args.message or "price and stock")
            )
            print(json.dumps(result, indent=2))
            if status in {"error", "blocked"}:
                raise SystemExit(1)
            return
        ai = create_ai()
        data = json.loads(args.request.read_text(encoding="utf-8"))
        if args.fresh_demo:
            if ai.provider.name != "mock":
                raise ValueError("--fresh-demo is only allowed with the offline mock provider")
            now = datetime.now(UTC)
            if data.get("product"):
                data["product"]["observed_at"] = now.isoformat()
            if data.get("authorization"):
                data["authorization"]["expires_at"] = (now + timedelta(minutes=2)).isoformat()
        request = AIRequest.model_validate(data)
        result = asyncio.run(ai.decide(request))
        print(result.model_dump_json(indent=2))
        if result.status in {"error", "blocked"}:
            raise SystemExit(1)
    except (OSError, ValueError, ValidationError) as exc:
        # Never print provider exceptions, credentials or complete invalid request bodies.
        print(
            f"Input/configuration error ({type(exc).__name__}); check README and request schema.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
