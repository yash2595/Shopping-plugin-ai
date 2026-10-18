"""Opt-in file -> live STT/model/TTS -> audio file smoke test. May incur charges.

No microphone, browser execution or persisted user session is provided.
"""

import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from shopping_ai import Principal
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


async def run(source: Path, output: Path):
    if await asyncio.to_thread(output.exists):
        raise ValueError("Output already exists")
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
            AudioEvent(turn=VoiceTurn(request_id=uuid4().hex, audio=audio)),
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
    args = parser.parse_args()
    try:
        asyncio.run(run(args.input, args.output))
    except (ValueError, OSError):
        raise SystemExit(
            "Check audio files and AI_CHAT/STT/TTS configuration; values are hidden."
        ) from None
    except KeyboardInterrupt:
        pass
