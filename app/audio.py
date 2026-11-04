"""ffmpeg helpers: join chunks, optionally level to ACX specs, and measure.

ACX (Audible) upload specs we check:
  RMS between -23 dB and -18 dB, peak no higher than -3 dB,
  0.5-1 s of room tone at the start and 1-5 s at the end.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ACX_RMS_RANGE = (-23.0, -18.0)
ACX_PEAK_MAX = -3.0
TARGET_RMS = -20.0
LIMITER_DB = -4.0          # keep 1 dB under ACX's -3 dB so MP3 encoding can't push peaks over
HEAD_SILENCE_MS = 750
TAIL_SILENCE_S = 2.0
MP3_ARGS = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100"]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout
    return round(float(out.strip()), 3)


def measure(path: Path) -> dict:
    """Overall RMS / peak / noise floor in dBFS, via ffmpeg's astats filter."""
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "astats=measure_perchannel=none", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    ).stderr
    overall = err[err.rfind("Overall"):] if "Overall" in err else err

    def grab(label: str) -> float | None:
        m = re.search(rf"{label}:\s*(-?inf|-?[\d.]+)", overall)
        if not m or "inf" in m.group(1):  # -inf (digital silence) isn't valid JSON; report as None
            return None
        return round(float(m.group(1)), 2)

    rms, peak = grab("RMS level dB"), grab("Peak level dB")
    return {"rms_db": rms, "peak_db": peak, "noise_floor_db": grab("Noise floor dB"), "duration_seconds": duration(path)}


def acx_check(m: dict) -> dict:
    rms_ok = m["rms_db"] is not None and ACX_RMS_RANGE[0] <= m["rms_db"] <= ACX_RMS_RANGE[1]
    peak_ok = m["peak_db"] is not None and m["peak_db"] <= ACX_PEAK_MAX
    return {"rms_ok": rms_ok, "peak_ok": peak_ok, "pass": rms_ok and peak_ok}


def _concat(inputs: list[Path], out: Path) -> None:
    args = ["ffmpeg", "-y", "-v", "error"]
    for p in inputs:
        args += ["-i", str(p)]
    labels = "".join(f"[{i}:a]" for i in range(len(inputs)))
    args += ["-filter_complex", f"{labels}concat=n={len(inputs)}:v=0:a=1,aresample=44100[a]",
             "-map", "[a]", "-ac", "1", "-c:a", "pcm_f32le", str(out)]
    _run(args)


def _level(src: Path, out: Path, gain_db: float) -> None:
    limit = 10 ** (LIMITER_DB / 20)
    chain = (f"volume={gain_db:.2f}dB,"
             f"alimiter=limit={limit:.4f}:level=false:attack=5:release=50,"
             f"adelay={HEAD_SILENCE_MS},apad=pad_dur={TAIL_SILENCE_S}")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", chain, "-c:a", "pcm_f32le", str(out)])


def _encode(src: Path, wav: Path, mp3: Path) -> None:
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", "44100", "-c:a", "pcm_s16le", str(wav)])
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), *MP3_ARGS, str(mp3)])


def export(run_dir: Path, chunk_files: list[str], acx: bool = False) -> dict:
    chunks = [run_dir / f for f in chunk_files]
    chunk_durations = [duration(p) for p in chunks]
    joined = run_dir / "_joined.wav"
    _concat(chunks, joined)

    master = joined
    gain = 0.0
    if acx:
        # Set the gain from the measured RMS, limit peaks, re-measure, and nudge once if needed.
        master = run_dir / "_leveled.wav"
        gain = TARGET_RMS - (measure(joined)["rms_db"] or TARGET_RMS)
        for _ in range(3):
            _level(joined, master, gain)
            rms = measure(master)["rms_db"]
            if rms is None or abs(rms - TARGET_RMS) <= 1.0:
                break
            gain += TARGET_RMS - rms

    wav, mp3 = run_dir / "audiobook.wav", run_dir / "audiobook.mp3"
    _encode(master, wav, mp3)
    for tmp in {joined, master}:
        tmp.unlink(missing_ok=True)

    offset = HEAD_SILENCE_MS / 1000 if acx else 0.0
    seams, t = [], offset
    for d in chunk_durations[:-1]:
        t += d
        seams.append(round(t, 3))

    m_mp3, m_wav = measure(mp3), measure(wav)
    return {
        "outputs": {"mp3": mp3.name, "wav": wav.name},
        "chunk_durations": chunk_durations,
        "seams_seconds": seams,
        "duration_seconds": m_mp3["duration_seconds"],
        "acx": {"leveled": acx, "gain_db": round(gain, 2), "measured_mp3": m_mp3, "measured_wav": m_wav,
                **acx_check(m_mp3)},
    }
