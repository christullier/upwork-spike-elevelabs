"""The stitching loop: generate chunks one at a time, each conditioned on the last.

Every chunk is saved to disk (plus run.json) the moment it finishes, so a
failed run can be resumed later without paying for finished chunks again.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import audio
from .chunker import split_text, word_count
from .pronounce import Rule, apply_aliases, ipa_rules
from .tts import MODEL_ID, OUTPUT_FORMAT, with_retries

MAX_PREVIOUS_IDS = 3              # API limit for previous_request_ids
REQUEST_ID_MAX_AGE = 2 * 3600 - 300  # IDs older than 2 h can't be used; keep 5 min margin
CONTEXT_CHARS = 1000              # how much neighbouring text to send as previous_text / next_text
RETRY_BASE_DELAY = 1.0            # seconds; backoff is 1s, 2s, 4s

Progress = Callable[[dict], None]


class ChunkFailed(Exception):
    def __init__(self, index: int, total: int, error: Exception):
        super().__init__(f"Chunk {index} of {total} failed: {error}")
        self.index, self.total, self.error = index, total, error


def _now() -> float:
    return time.time()


def fingerprint(chunks: list[str], voice_id: str, model_id: str, rules: list[Rule]) -> str:
    payload = json.dumps([chunks, voice_id, model_id, [r.to_dict() for r in rules]], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def save_manifest(run_dir: Path, manifest: dict) -> None:
    tmp = run_dir / "run.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    os.replace(tmp, run_dir / "run.json")


def load_manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text())


def find_resumable(output_root: Path, fp: str) -> Path | None:
    """Newest unfinished run with the same text, voice, model and rules."""
    if not output_root.exists():
        return None
    for run_dir in sorted(output_root.iterdir(), reverse=True):
        try:
            m = load_manifest(run_dir)
        except (OSError, ValueError):
            continue
        if m.get("fingerprint") == fp and m.get("status") != "complete":
            return run_dir
    return None


def new_run_dir(output_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir, n = output_root / stamp, 1
    while run_dir.exists():
        n += 1
        run_dir = output_root / f"{stamp}-{n}"
    run_dir.mkdir(parents=True)
    return run_dir


def prepare(text: str, rules: list[Rule], chunk_limit: int) -> tuple[str, list[str]]:
    spoken = apply_aliases(text, rules)
    return spoken, split_text(spoken, chunk_limit)


def estimate(text: str, rules: list[Rule], chunk_limit: int) -> dict:
    spoken, chunks = prepare(text, rules, chunk_limit)
    return {
        "words": word_count(text),
        "characters": len(text),
        "characters_billed": sum(len(c) for c in chunks),
        "chunks": [len(c) for c in chunks],
    }


def _trim_tail(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    tail = text[-n:]
    return tail[tail.find(" ") + 1:] if " " in tail else tail


def _trim_head(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    head = text[:n]
    return head[:head.rfind(" ")] if " " in head else head


def stitching_context(chunks: list[str], entries: list[dict], i: int, now: float) -> dict:
    """What to send alongside chunk i (0-based) so it continues smoothly.

    - previous_request_ids: up to 3 earlier chunks' IDs, closest last, only if
      younger than 2 hours (API rule).
    - previous_text: only when no usable IDs (the API ignores it otherwise).
    - next_text: the start of the following chunk, always when there is one.
    """
    ids = [
        e["request_id"] for e in entries[max(0, i - MAX_PREVIOUS_IDS):i]
        if e.get("request_id") and e.get("completed_at") and now - e["completed_at"] < REQUEST_ID_MAX_AGE
    ]
    ctx: dict = {"previous_request_ids": ids or None, "previous_text": None, "next_text": None}
    if i > 0 and not ids:
        ctx["previous_text"] = _trim_tail(chunks[i - 1], CONTEXT_CHARS)
    if i + 1 < len(chunks):
        ctx["next_text"] = _trim_head(chunks[i + 1], CONTEXT_CHARS)
    return ctx


def run_chunks(run_dir: Path, manifest: dict, tts, progress: Progress = lambda _: None,
               now: Callable[[], float] = _now, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Generate every chunk not already on disk, strictly in order."""
    entries = manifest["chunks"]
    chunks = [e["text"] for e in entries]
    total = len(chunks)

    for i, text in enumerate(chunks):
        entry = entries[i]
        if entry["status"] == "done" and (run_dir / entry["file"]).exists():
            progress({"stage": "chunks", "done": i + 1, "total": total, "message": f"Chunk {i + 1} of {total} already saved, skipping"})
            continue

        progress({"stage": "chunks", "done": i, "total": total, "message": f"Generating chunk {i + 1} of {total}"})
        ctx = stitching_context(chunks, entries, i, now())
        started = now()

        def on_retry(attempt, exc, delay, _i=i):
            progress({"stage": "chunks", "done": _i, "total": total,
                      "message": f"Chunk {_i + 1} of {total}: {exc} — retrying in {delay:.0f}s (attempt {attempt + 1})"})

        try:
            result = with_retries(
                lambda: tts.synthesize(text, manifest["voice"]["id"], manifest["model_id"],
                                       dictionaries=manifest.get("pronunciation_dictionaries"),
                                       output_format=manifest["output_format"], **ctx),
                sleep=sleep, on_retry=on_retry, base_delay=RETRY_BASE_DELAY,
            )
        except Exception as exc:  # noqa: BLE001
            entry["status"] = "failed"
            manifest.update(status="failed", failed_chunk=i + 1, error=str(exc))
            save_manifest(run_dir, manifest)
            raise ChunkFailed(i + 1, total, exc) from exc

        (run_dir / entry["file"]).write_bytes(result.audio)
        entry.update(
            status="done", request_id=result.request_id, completed_at=now(),
            generation_seconds=round(now() - started, 2),
            character_cost=result.character_cost if result.character_cost is not None else len(text),
            context={"previous_request_ids": ctx["previous_request_ids"],
                     "previous_text_chars": len(ctx["previous_text"] or ""),
                     "next_text_chars": len(ctx["next_text"] or "")},
        )
        manifest["characters_used"] = sum(e.get("character_cost") or 0 for e in entries)
        manifest.update(status="running", failed_chunk=None, error=None)
        save_manifest(run_dir, manifest)
        progress({"stage": "chunks", "done": i + 1, "total": total, "message": f"Chunk {i + 1} of {total} done"})

    return manifest


def start_or_resume(output_root: Path, text: str, voice_id: str, voice_name: str, rules: list[Rule],
                    chunk_limit: int, tts, model_id: str = MODEL_ID) -> tuple[Path, dict, bool]:
    """Create a new run folder, or reopen an unfinished one with identical inputs."""
    _, chunks = prepare(text, rules, chunk_limit)
    if not chunks:
        raise ValueError("No text to generate.")
    fp = fingerprint(chunks, voice_id, model_id, rules)
    existing = find_resumable(output_root, fp)
    if existing:
        return existing, load_manifest(existing), True

    run_dir = new_run_dir(output_root)
    (run_dir / "input.txt").write_text(text)
    dictionaries = None
    if ipa_rules(rules):
        dictionaries = [tts.create_dictionary(f"audiobook-{run_dir.name}", ipa_rules(rules))]
    manifest = {
        "run_id": run_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "running",
        "backend": tts.name,
        "model_id": model_id,
        "output_format": OUTPUT_FORMAT,
        "voice": {"id": voice_id, "name": voice_name},
        "chunk_limit": chunk_limit,
        "fingerprint": fp,
        "pronunciations": [r.to_dict() for r in rules],
        "pronunciation_dictionaries": dictionaries,
        "words": word_count(text),
        "characters_input": len(text),
        "characters_estimated": sum(len(c) for c in chunks),
        "characters_used": 0,
        "chunks": [
            {"index": n + 1, "file": f"chunk_{n + 1:02d}.mp3", "characters": len(c), "text": c,
             "status": "pending", "request_id": None}
            for n, c in enumerate(chunks)
        ],
        "failed_chunk": None,
        "error": None,
    }
    save_manifest(run_dir, manifest)
    return run_dir, manifest, False


def generate(output_root: Path, text: str, voice_id: str, voice_name: str, rules: list[Rule],
             chunk_limit: int, tts, acx: bool = False, progress: Progress = lambda _: None) -> tuple[Path, dict]:
    """Whole job: chunks -> join -> (optional) ACX leveling -> measure -> run.json."""
    run_dir, manifest, resumed = start_or_resume(output_root, text, voice_id, voice_name, rules, chunk_limit, tts)
    progress({"stage": "start", "run_id": run_dir.name, "resumed": resumed, "done": 0, "total": len(manifest["chunks"]),
              "message": "Resuming previous run" if resumed else "Starting"})
    run_chunks(run_dir, manifest, tts, progress)

    progress({"stage": "export", "message": "Joining chunks with ffmpeg"})
    result = audio.export(run_dir, [e["file"] for e in manifest["chunks"]], acx=acx)
    manifest.update(status="complete", **result)
    save_manifest(run_dir, manifest)
    progress({"stage": "complete", "message": "Done"})
    return run_dir, manifest
