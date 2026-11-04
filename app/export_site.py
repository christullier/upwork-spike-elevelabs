"""Turn one finished run into a static "replay" site for free hosting.

The exported folder has no server code and no API key: the page plays back the
saved run (real audio, real request IDs, real measurements) and says so.
Deploy the folder as-is to Cloudflare Pages, Vercel, Netlify or GitHub Pages.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
COPY = ["run.json", "input.txt", "audiobook.mp3", "audiobook.wav"]
OPTIONAL = ["words.json"]  # word timings for the clickable transcript (scripts/align_words.py)


def export(run_dir: Path, out: Path) -> Path:
    manifest = json.loads((run_dir / "run.json").read_text())
    if manifest.get("status") != "complete":
        raise SystemExit(f"{run_dir} is not a complete run (status: {manifest.get('status')}).")
    if out.exists():
        shutil.rmtree(out)
    (out / "demo").mkdir(parents=True)
    shutil.copy(STATIC / "index.html", out / "index.html")
    for name in COPY:
        shutil.copy(run_dir / name, out / "demo" / name)
    for name in OPTIONAL:
        if (run_dir / name).exists():
            shutil.copy(run_dir / name, out / "demo" / name)
    (out / "config.json").write_text(json.dumps({"mode": "replay", "model_id": manifest["model_id"]}, indent=2))
    return out


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m app export-site")
    parser.add_argument("run_dir", type=Path, help="a finished run, e.g. output/20261104-120000")
    parser.add_argument("--out", type=Path, default=Path("site"))
    args = parser.parse_args(argv)
    out = export(args.run_dir, args.out)
    print(f"Static demo written to {out}/ — deploy that folder (see README, 'Free hosting').")
