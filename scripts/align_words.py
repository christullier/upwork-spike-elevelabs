"""Write words.json (per-word start/end times) for a finished run, for the clickable transcript.

Uses faster-whisper locally, so no ElevenLabs credits or extra API permissions are needed:

    pip install faster-whisper
    python scripts/align_words.py output/<run_id>

Whisper's words are matched one-to-one onto the original input.txt, so the transcript shows
the author's spelling (e.g. "Irene", not the pronunciation alias). If the counts don't match,
the script stops rather than guess.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel


def main(run_dir: Path, model: str = "medium.en") -> None:
    run = json.loads((run_dir / "run.json").read_text())
    tokens = (run_dir / "input.txt").read_text().split()
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(run_dir / run["outputs"]["mp3"]),
                          "-f", "s16le", "-ar", "16000", "-ac", "1", "-"], check=True, capture_output=True).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    segments, _ = WhisperModel(model, device="cpu", compute_type="int8").transcribe(audio, word_timestamps=True, beam_size=5)
    heard = [w for s in segments for w in s.words]
    if len(heard) != len(tokens):
        raise SystemExit(f"Whisper heard {len(heard)} words but the text has {len(tokens)}; not writing words.json.")
    norm = lambda s: re.sub(r"\W", "", s.lower())
    for h, t in zip(heard, tokens):
        if norm(h.word) != norm(t):
            print(f"note: heard {h.word.strip()!r} for {t!r}")
    words = [[t, round(h.start, 2), round(h.end, 2)] for t, h in zip(tokens, heard)]
    (run_dir / "words.json").write_text(json.dumps({"source": f"faster-whisper {model}", "words": words}, separators=(",", ":")))
    print(f"Wrote {run_dir / 'words.json'} ({len(words)} words).")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
