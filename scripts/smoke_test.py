"""One short real request (~200 characters) to confirm the key, the model and the plan work.

    uv run python scripts/smoke_test.py [--voice VOICE_ID]

Costs about 200 characters. Writes output/smoke_test.mp3.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio import duration  # noqa: E402
from app.tts import MODEL_ID, OUTPUT_FORMAT, ElevenLabsTTS, TTSError  # noqa: E402

TEXT = ("This is a short test of the audiobook pipeline. If you can hear this clearly, "
        "the API key works, the Eleven v4 model is available on this account, and the "
        "high quality MP3 format is enabled.")
NEXT = "The next chapter begins on a cold morning in London."
GEORGE = "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs premade narrator voice


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default=GEORGE)
    args = parser.parse_args()

    tts = ElevenLabsTTS()
    models = {m.model_id for m in tts.client.models.list()}
    print(f"eleven_v4 available: {MODEL_ID in models}")

    print(f"Sending {len(TEXT)} characters (+ {len(NEXT)} chars of next_text context) …")
    try:
        result = tts.synthesize(TEXT, args.voice, MODEL_ID, next_text=NEXT)
    except TTSError as exc:
        sys.exit(f"Failed: {exc}")

    out = Path("output")
    out.mkdir(exist_ok=True)
    path = out / "smoke_test.mp3"
    path.write_bytes(result.audio)
    print(f"OK  request-id={result.request_id}")
    print(f"    billed characters={result.character_cost} (text was {len(TEXT)}; "
          f"{'next_text was not billed' if result.character_cost == len(TEXT) else 'compare with text length'})")
    print(f"    {OUTPUT_FORMAT}, {len(result.audio):,} bytes, {duration(path):.1f}s → {path}")


if __name__ == "__main__":
    main()
