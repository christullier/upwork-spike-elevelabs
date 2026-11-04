"""Full pipeline through the HTTP API with the mock voice and real ffmpeg (free)."""

import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from app import server
from app.export_site import export
from app.tts import MockTTS

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT", tmp_path / "output")
    return TestClient(server.create_app(MockTTS(delay=0)))


def wait(client, job_id):
    for _ in range(200):
        s = client.get(f"/api/jobs/{job_id}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_generate_excerpt_with_acx(client, tmp_path):
    text = client.get("/api/sample/excerpt").json()["text"]
    rules = [{"word": "Irene", "say": "Eye-ree-nee", "kind": "alias"}]
    est = client.post("/api/estimate", json={"text": text, "rules": rules, "chunk_limit": 300}).json()
    assert len(est["chunks"]) == 2

    job = client.post("/api/generate", json={"text": text, "voice_id": "mock-narrator", "voice_name": "Mock",
                                             "rules": rules, "chunk_limit": 300, "acx": True}).json()
    status = wait(client, job["job_id"])
    assert status["status"] == "complete", status

    run_dir = tmp_path / "output" / status["run_id"]
    run = json.loads((run_dir / "run.json").read_text())
    assert run["characters_used"] == est["characters_billed"]
    assert len(run["seams_seconds"]) == 1
    assert run["acx"]["pass"], run["acx"]
    assert -23 <= run["acx"]["measured_mp3"]["rms_db"] <= -18
    assert run["acx"]["measured_mp3"]["peak_db"] <= -3
    for name in ("audiobook.mp3", "audiobook.wav", "chunk_01.mp3", "chunk_02.mp3", "input.txt"):
        assert (run_dir / name).exists()
    assert client.get(f"/output/{run['run_id']}/audiobook.mp3").status_code == 200

    site = export(run_dir, tmp_path / "site")
    assert json.loads((site / "config.json").read_text())["mode"] == "replay"
    assert (site / "demo" / "audiobook.mp3").exists() and (site / "index.html").exists()


def test_failed_chunk_reported_then_resumed(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.pipeline.RETRY_BASE_DELAY", 0.01)
    failing = server.create_app(MockTTS(delay=0, fail="1:4"))  # chunk 2 fails all 4 attempts
    c = TestClient(failing)
    text = c.get("/api/sample/excerpt").json()["text"]
    body = {"text": text, "voice_id": "mock-narrator", "rules": [], "chunk_limit": 300}

    s = wait(c, c.post("/api/generate", json=body).json()["job_id"])
    assert s["status"] == "failed" and s["failed_chunk"] == 2 and "Chunk 2 of 2" in s["error"]

    s2 = wait(c, c.post("/api/generate", json=body).json()["job_id"])  # same app: mock now succeeds
    assert s2["status"] == "complete" and s2["run_id"] == s["run_id"]
    run = json.loads((tmp_path / "output" / s2["run_id"] / "run.json").read_text())
    assert run["chunks"][1]["context"]["previous_request_ids"] == [run["chunks"][0]["request_id"]]


def test_clone_requires_consent(client):
    files = [("files", ("a.mp3", b"123", "audio/mpeg"))]
    assert client.post("/api/clone", data={"name": "X"}, files=files).status_code == 400
    r = client.post("/api/clone", data={"name": "X", "consent": "true"}, files=files)
    assert r.status_code == 200 and r.json()["voice_id"].startswith("mock-clone")
