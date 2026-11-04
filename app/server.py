"""FastAPI app: one static page plus a small JSON API."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import pipeline
from .chunker import DEFAULT_LIMIT
from .pronounce import parse_rules
from .tts import MODEL_ID

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
OUTPUT = ROOT / "output"
SAMPLES = {"chapter": ROOT / "samples" / "chapter.txt", "excerpt": ROOT / "samples" / "excerpt.txt"}
MAX_SAMPLE_BYTES = 10 * 1024 * 1024


class TextRequest(BaseModel):
    text: str
    rules: list[dict] = []
    chunk_limit: int = Field(DEFAULT_LIMIT, ge=100, le=10_000)


class GenerateRequest(TextRequest):
    voice_id: str
    voice_name: str = ""
    acx: bool = False


def strip_source_note(text: str) -> str:
    """Sample files start with a source note ending in a '----' line; drop it."""
    head, sep, body = text.partition("\n----\n")
    return body.strip() if sep else text.strip()


def create_app(tts) -> FastAPI:
    app = FastAPI(title="Audiobook TTS")
    jobs: dict[str, dict] = {}
    busy = threading.Lock()
    OUTPUT.mkdir(exist_ok=True)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/config.json")
    def config():
        return {"mode": "mock" if tts.name == "mock" else "live", "model_id": MODEL_ID, "chunk_limit": DEFAULT_LIMIT}

    @app.get("/api/sample/{name}")
    def sample(name: str):
        if name not in SAMPLES:
            raise HTTPException(404, "Unknown sample")
        return {"text": strip_source_note(SAMPLES[name].read_text())}

    @app.get("/api/voices")
    def voices():
        try:
            return tts.list_voices()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Could not load voices: {exc}") from exc

    @app.post("/api/clone")
    async def clone(name: str = Form(...), consent: bool = Form(False), files: list[UploadFile] = File(...)):
        if not consent:
            raise HTTPException(400, "Please confirm you have permission to clone this voice.")
        if not 1 <= len(files) <= 3:
            raise HTTPException(400, "Upload 1 to 3 audio samples.")
        samples = []
        for f in files:
            data = await f.read()
            if len(data) > MAX_SAMPLE_BYTES:
                raise HTTPException(400, f"{f.filename} is over 10 MB.")
            samples.append((f.filename or "sample", data))
        try:
            return {"voice_id": tts.clone_voice(name.strip(), samples), "name": name.strip()}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Cloning failed: {exc}") from exc

    @app.post("/api/estimate")
    def estimate(req: TextRequest):
        return pipeline.estimate(req.text, parse_rules(req.rules), req.chunk_limit)

    @app.post("/api/generate")
    def generate(req: GenerateRequest):
        if not req.text.strip():
            raise HTTPException(400, "Paste some text first.")
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "A generation is already running.")
        job_id = uuid.uuid4().hex[:8]
        job = jobs[job_id] = {"status": "running", "stage": "start", "done": 0, "total": 0, "message": "Starting",
                              "run_id": None, "error": None, "failed_chunk": None}

        def progress(update: dict):
            job.update(update)

        def work():
            try:
                run_dir, _ = pipeline.generate(OUTPUT, req.text, req.voice_id, req.voice_name or req.voice_id,
                                               parse_rules(req.rules), req.chunk_limit, tts, acx=req.acx, progress=progress)
                job.update(status="complete", run_id=run_dir.name)
            except pipeline.ChunkFailed as exc:
                job.update(status="failed", failed_chunk=exc.index, total=exc.total,
                           error=f"Chunk {exc.index} of {exc.total} failed after retries: {exc.error}")
            except Exception as exc:  # noqa: BLE001
                job.update(status="failed", error=str(exc))
            finally:
                busy.release()

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, "Unknown job")
        return jobs[job_id]

    app.mount("/output", StaticFiles(directory=OUTPUT), name="output")
    return app
