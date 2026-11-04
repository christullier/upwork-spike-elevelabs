"""Entry point.

    python -m app                 # real ElevenLabs API (needs ELEVENLABS_API_KEY)
    python -m app --mock          # free: tone clips instead of speech (or MOCK_TTS=1)
    python -m app export-site output/<run> [--out site]   # static replay demo for free hosting
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv


def serve(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--mock", action="store_true", help="use free tone clips instead of ElevenLabs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    from .audio import ffmpeg_available

    if not ffmpeg_available():
        sys.exit("ffmpeg/ffprobe not found. Install it first (macOS: brew install ffmpeg, Ubuntu: sudo apt install ffmpeg).")

    mock = args.mock or os.getenv("MOCK_TTS") == "1"
    if not mock and not os.getenv("ELEVENLABS_API_KEY"):
        sys.exit("ELEVENLABS_API_KEY is not set. Add it to .env (see .env.example), or run with --mock.")

    import uvicorn

    from .server import create_app
    from .tts import make_tts

    print(f"Mode: {'MOCK (no API calls)' if mock else 'ElevenLabs'}  →  http://localhost:{args.port}")
    uvicorn.run(create_app(make_tts(mock)), host=args.host, port=args.port, log_level="warning")


def main() -> None:
    load_dotenv()  # real environment variables win over .env
    argv = sys.argv[1:]
    if argv and argv[0] == "export-site":
        from .export_site import main as export_main

        export_main(argv[1:])
    else:
        serve(argv)


if __name__ == "__main__":
    main()
