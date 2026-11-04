"""The stitching loop, run against a fake client (no API, no ffmpeg)."""

import pytest

from app import pipeline
from app.pronounce import Rule
from app.tts import TTSError, TTSResult, with_retries

TEXT = "\n\n".join(f"Paragraph {i}. " + "It was a dark and stormy night. " * 4 for i in range(6))


class FakeTTS:
    name = "fake"

    def __init__(self, fail_at: int | None = None, fail_times: int = 99, status: int = 503):
        self.calls, self.fail_at, self.fail_times, self.status = [], fail_at, fail_times, status
        self.finished = []  # chunk texts whose audio was fully returned, in order

    def synthesize(self, text, voice_id, model_id, **kw):
        n = len(self.finished)
        self.calls.append({"text": text, "voice_id": voice_id, "model_id": model_id, "n": n, **kw})
        if self.fail_at == n and self.fail_times > 0:
            self.fail_times -= 1
            raise TTSError(f"fake {self.status}", self.status)
        self.finished.append(text)
        return TTSResult(audio=b"ID3fake", request_id=f"req-{n + 1}", character_cost=len(text))

    def create_dictionary(self, name, rules):
        return {"pronunciation_dictionary_id": "dict-1", "version_id": "v1"}


def start(tmp_path, tts, rules=(), limit=200):
    run_dir, manifest, resumed = pipeline.start_or_resume(tmp_path, TEXT, "voice-1", "Voice One", list(rules), limit, tts)
    return run_dir, manifest, resumed


def no_sleep(_):
    pass


def test_chunks_generated_in_order_with_request_ids_forwarded(tmp_path):
    tts = FakeTTS()
    run_dir, manifest, _ = start(tmp_path, tts)
    pipeline.run_chunks(run_dir, manifest, tts, sleep=no_sleep)
    n = len(manifest["chunks"])
    assert n >= 5

    assert [c["text"] for c in tts.calls] == [e["text"] for e in manifest["chunks"]]
    first = tts.calls[0]
    assert first["previous_request_ids"] is None and first["previous_text"] is None
    assert first["next_text"] and manifest["chunks"][1]["text"].startswith(first["next_text"][:20])

    for i, call in enumerate(tts.calls[1:], start=1):
        expected = [f"req-{k}" for k in range(max(1, i - 2), i + 1)]  # last 3 IDs, closest last
        assert call["previous_request_ids"] == expected
        assert call["previous_text"] is None  # API ignores it when IDs are sent, so we don't send it
        assert all(rid in [f"req-{k}" for k in range(1, len(tts.finished) + 1)] for rid in expected)
    assert tts.calls[-1]["next_text"] is None
    assert all(c["model_id"] == "eleven_v4" and c["voice_id"] == "voice-1" for c in tts.calls)


def test_request_id_only_used_after_its_chunk_finished(tmp_path):
    tts = FakeTTS()
    run_dir, manifest, _ = start(tmp_path, tts)
    pipeline.run_chunks(run_dir, manifest, tts, sleep=no_sleep)
    for call in tts.calls:
        finished_before = {f"req-{k}" for k in range(1, call["n"] + 1)}
        assert set(call["previous_request_ids"] or []) <= finished_before


def test_manifest_records_everything(tmp_path):
    tts = FakeTTS()
    run_dir, manifest, _ = start(tmp_path, tts)
    pipeline.run_chunks(run_dir, manifest, tts, sleep=no_sleep)
    saved = pipeline.load_manifest(run_dir)
    assert saved["model_id"] == "eleven_v4" and saved["voice"] == {"id": "voice-1", "name": "Voice One"}
    assert [e["request_id"] for e in saved["chunks"]] == [f"req-{i}" for i in range(1, len(saved["chunks"]) + 1)]
    assert saved["characters_used"] == sum(e["characters"] for e in saved["chunks"])
    assert all((run_dir / e["file"]).read_bytes() == b"ID3fake" for e in saved["chunks"])


def test_failure_stops_and_names_chunk_then_resume_skips_paid_chunks(tmp_path):
    tts = FakeTTS(fail_at=2)  # third chunk always fails
    run_dir, manifest, _ = start(tmp_path, tts)
    with pytest.raises(pipeline.ChunkFailed) as err:
        pipeline.run_chunks(run_dir, manifest, tts, sleep=no_sleep)
    assert err.value.index == 3
    saved = pipeline.load_manifest(run_dir)
    assert saved["status"] == "failed" and saved["failed_chunk"] == 3
    assert [e["status"] for e in saved["chunks"][:3]] == ["done", "done", "failed"]
    assert len(tts.calls) == 2 + 4  # two good chunks, then 4 attempts at chunk 3

    # Re-run with the same inputs: same folder, chunks 1-2 not requested again.
    tts2 = FakeTTS()
    tts2.finished = ["x", "y"]  # so its request IDs continue at req-3
    run_dir2, manifest2, resumed = start(tmp_path, tts2)
    assert resumed and run_dir2 == run_dir
    pipeline.run_chunks(run_dir2, manifest2, tts2, sleep=no_sleep)
    assert tts2.calls[0]["text"] == manifest2["chunks"][2]["text"]
    assert tts2.calls[0]["previous_request_ids"] == ["req-1", "req-2"]  # IDs from the first session carried over
    assert len(tts2.calls) == len(manifest2["chunks"]) - 2


def test_stale_request_ids_fall_back_to_previous_text(tmp_path):
    tts = FakeTTS(fail_at=1)
    run_dir, manifest, _ = start(tmp_path, tts)
    with pytest.raises(pipeline.ChunkFailed):
        pipeline.run_chunks(run_dir, manifest, tts, sleep=no_sleep)

    later = lambda: manifest["chunks"][0]["completed_at"] + 3 * 3600  # resume 3 hours later  # noqa: E731
    tts2 = FakeTTS()
    pipeline.run_chunks(run_dir, manifest, tts2, now=later, sleep=no_sleep)
    first = tts2.calls[0]
    assert first["previous_request_ids"] is None
    assert first["previous_text"] and manifest["chunks"][0]["text"].endswith(first["previous_text"])


def test_non_retryable_error_is_not_retried(tmp_path):
    tts = FakeTTS(fail_at=0, status=401)
    run_dir, manifest, _ = start(tmp_path, tts)
    with pytest.raises(pipeline.ChunkFailed):
        pipeline.run_chunks(run_dir, manifest, tts, sleep=no_sleep)
    assert len(tts.calls) == 1


def test_retry_recovers_from_transient_429():
    attempts, delays = [], []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise TTSError("rate limited", 429)
        return TTSResult(b"ok", "req-1")

    assert with_retries(flaky, sleep=delays.append).request_id == "req-1"
    assert len(attempts) == 3 and delays[0] < delays[1]


def test_aliases_applied_before_chunking_and_ipa_becomes_dictionary(tmp_path):
    tts = FakeTTS()
    rules = [Rule("stormy", "STORMY-ALIAS"), Rule("night", "naɪt", "ipa")]
    run_dir, manifest, _ = start(tmp_path, tts, rules=rules)
    pipeline.run_chunks(run_dir, manifest, tts, sleep=no_sleep)
    assert all("STORMY-ALIAS" in c["text"] and "stormy" not in c["text"] for c in tts.calls)
    assert all(c["dictionaries"] == [{"pronunciation_dictionary_id": "dict-1", "version_id": "v1"}] for c in tts.calls)


def test_estimate_counts_alias_text():
    e = pipeline.estimate("Siobhan said hi.", [Rule("Siobhan", "Shiv-awn")], 9000)
    assert e == {"words": 3, "characters": 16, "characters_billed": 17, "chunks": [17]}
