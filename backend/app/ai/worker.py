"""Run durable deadline settlement: python -m app.ai.worker --once.

No browser is launched. Work comes from PostgreSQL, including after restart.
"""

import argparse
import asyncio
import signal
from pathlib import Path

from app.ai.runtime import configured_runtime
from app.config import get_settings
from app.db import SessionLocal
from app.services.runtime_service import RuntimeService


async def run(*, once=False, interval=2.0, limit=20, heartbeat_file=None):
    settings = get_settings()
    if not settings.AI_RUNTIME_ENABLED:
        raise RuntimeError("AI_RUNTIME_ENABLED is required")
    stopped = asyncio.Event()
    previous = signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    try:
        async with configured_runtime(settings) as runtime:
            service = RuntimeService(runtime, settings)
            while not stopped.is_set():
                try:
                    with SessionLocal() as db:
                        count = await service.settle_due(db, limit)
                    if heartbeat_file is not None:
                        Path(heartbeat_file).touch()
                    if once:
                        print(f"Settled {count} due AI dispatches.")
                        return
                except Exception:
                    if once:
                        raise RuntimeError("AI_DEADLINE_SETTLEMENT_FAILED") from None
                    # Deliberately omit exception strings/SQL parameters.
                    print("AI_DEADLINE_SETTLEMENT_RETRY")
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=interval)
                except TimeoutError:
                    pass
    finally:
        signal.signal(signal.SIGTERM, previous)


def main():
    parser = argparse.ArgumentParser(description="Settle committed AI action deadlines")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--heartbeat-file", help="Touch only after successful deadline settlement")
    args = parser.parse_args()
    if not 0.1 <= args.interval <= 60 or not 1 <= args.limit <= 100:
        parser.error("interval must be 0.1-60 and limit 1-100")
    asyncio.run(
        run(
            once=args.once,
            interval=args.interval,
            limit=args.limit,
            heartbeat_file=args.heartbeat_file,
        )
    )


if __name__ == "__main__":
    main()
