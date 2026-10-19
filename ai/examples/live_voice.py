"""Opt-in file -> live STT/model/TTS -> audio file smoke test. May incur charges.

No microphone, browser execution or persisted user session is provided.
"""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from shopping_ai import ConversationContext, Principal
from shopping_ai.page_evidence import (
    MAX_CAPTURE_BYTES,
    decode_captured_page,
    extract_product_snapshot,
)
from shopping_ai.providers.chat import ChatSettings
from shopping_ai.providers.speech import SpeechSettings
from shopping_ai.runtime import AudioEvent, live_runtime
from shopping_ai.voice import AudioClip, VoiceTurn


def read_audio(source: Path) -> AudioClip:
    with source.open("rb") as handle:
        return AudioClip(format=source.suffix.lower().lstrip("."), data=handle.read(5_000_001))


def write_audio(output: Path, data: bytes):
    with output.open("xb") as handle:
        handle.write(data)


def read_context(source: Path, product_id: str) -> ConversationContext:
    with source.open("rb") as handle:
        page = decode_captured_page(handle.read(MAX_CAPTURE_BYTES + 1))
    snapshot = extract_product_snapshot(
        page, snapshot_id=uuid4().hex, product_id=product_id, now=datetime.now(UTC)
    )
    return ConversationContext(products=(snapshot,), target=page.target, available_tools=())


async def run(
    source: Path, output: Path, *, capture: Path | None = None, product_id: str | None = None
):
    if (capture is None) != (product_id is None):
        raise ValueError("Capture and product ID must be supplied together")
    if await asyncio.to_thread(output.exists):
        raise ValueError("Output already exists")
    context = (
        await asyncio.to_thread(read_context, capture, product_id)
        if capture is not None
        else ConversationContext(available_tools=())
    )
    settings = SpeechSettings.from_env()
    if output.suffix.lower() != "." + settings.output_format:
        raise ValueError("Output extension must match AI_TTS_FORMAT")
    audio = await asyncio.to_thread(read_audio, source)
    async with live_runtime(ChatSettings.from_env(), speech=settings) as assistant:
        principal = Principal(owner_id="local-voice-demo", session_id=uuid4().hex)
        state = (await assistant.start(principal)).state
        output_event = await assistant.handle(
            state,
            principal,
            AudioEvent(turn=VoiceTurn(request_id=uuid4().hex, audio=audio, **context.model_dump())),
            expected_version=state.version,
        )
        result = output_event.reply
        print(result.message)
        if result.audio is None:
            raise SystemExit("No audio produced; check configured speech services.")
        await asyncio.to_thread(write_audio, output, result.audio.data)
        print("AI-generated speech saved:", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--capture", type=Path, help="Fresh CapturedPage JSON for read-only product context"
    )
    parser.add_argument("--product-id", help="Trusted product ID; requires --capture")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.input, args.output, capture=args.capture, product_id=args.product_id))
    except (ValueError, OSError):
        raise SystemExit(
            "Check audio/capture files and AI_CHAT/STT/TTS configuration; values are hidden."
        ) from None
    except KeyboardInterrupt:
        pass
