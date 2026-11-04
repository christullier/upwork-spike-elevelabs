"""Text-to-speech backends: the real ElevenLabs client and a free mock.

Both expose the same small interface used by the pipeline and the server:
    synthesize(...) -> TTSResult, list_voices(), clone_voice(), create_dictionary()
"""

from __future__ import annotations

import os
import random
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Callable

MODEL_ID = "eleven_v4"
OUTPUT_FORMAT = "mp3_44100_192"  # 44.1 kHz / 192 kbps MP3 (Creator plan or above)
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class TTSResult:
    audio: bytes
    request_id: str
    character_cost: int | None = None


class TTSError(Exception):
    def __init__(self, message: str, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status in RETRY_STATUS
    # Network-level failures (timeouts, dropped connections) have no status.
    return exc.__class__.__name__ in {"ConnectError", "ReadTimeout", "WriteTimeout", "RemoteProtocolError", "ReadError", "TimeoutException"}


def with_retries(fn: Callable[[], TTSResult], attempts: int = 4, base_delay: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep,
                 on_retry: Callable[[int, Exception, float], None] | None = None) -> TTSResult:
    """Call fn, retrying 429/5xx/network errors with exponential backoff (1s, 2s, 4s...)."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - we re-raise anything we don't retry
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = getattr(exc, "retry_after", None) or base_delay * 2 ** (attempt - 1)
            delay = min(delay, 30) + random.uniform(0, 0.25 * base_delay)
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, api_key: str | None = None):
        from elevenlabs import ElevenLabs

        # The SDK reads ELEVENLABS_API_KEY from the environment when api_key is None.
        self.client = ElevenLabs(api_key=api_key) if api_key else ElevenLabs()

    def list_voices(self) -> list[dict]:
        resp = self.client.voices.get_all()
        return [
            {"voice_id": v.voice_id, "name": v.name, "category": v.category,
             "preview_url": v.preview_url, "labels": v.labels or {}}
            for v in resp.voices
        ]

    def clone_voice(self, name: str, files: list[tuple[str, bytes]], description: str | None = None) -> str:
        voice = self.client.voices.ivc.create(
            name=name, files=[(fname, data) for fname, data in files],
            description=description or "Instant voice clone created by the audiobook demo",
        )
        return voice.voice_id

    def create_dictionary(self, name: str, rules: list) -> dict:
        from elevenlabs.pronunciation_dictionaries.types import (
            BodyAddAPronunciationDictionaryV1PronunciationDictionariesAddFromRulesPostRulesItem_Phoneme as Phoneme,
        )

        d = self.client.pronunciation_dictionaries.create_from_rules(
            name=name,
            rules=[Phoneme(string_to_replace=r.word, phoneme=r.say, alphabet="ipa", case_sensitive=False) for r in rules],
        )
        return {"pronunciation_dictionary_id": d.id, "version_id": d.version_id}

    def synthesize(self, text: str, voice_id: str, model_id: str = MODEL_ID, *,
                   previous_request_ids: list[str] | None = None, previous_text: str | None = None,
                   next_text: str | None = None, dictionaries: list[dict] | None = None,
                   output_format: str = OUTPUT_FORMAT) -> TTSResult:
        from elevenlabs import PronunciationDictionaryVersionLocator
        from elevenlabs.core.api_error import ApiError

        kwargs: dict = {"text": text, "model_id": model_id, "output_format": output_format,
                        # We do our own retries (with logging), so turn off the SDK's.
                        "request_options": {"max_retries": 0, "timeout_in_seconds": 600}}
        # Docs: previous_text is ignored when previous_request_ids is sent, so send one or the other.
        if previous_request_ids:
            kwargs["previous_request_ids"] = previous_request_ids[-3:]
        elif previous_text:
            kwargs["previous_text"] = previous_text
        if next_text:
            kwargs["next_text"] = next_text
        if dictionaries:
            kwargs["pronunciation_dictionary_locators"] = [PronunciationDictionaryVersionLocator(**d) for d in dictionaries]

        try:
            with self.client.text_to_speech.with_raw_response.convert(voice_id, **kwargs) as resp:
                audio = b"".join(resp.data)  # read the whole body before the request ID is reused
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except ApiError as exc:
            retry_after = (exc.headers or {}).get("retry-after")
            raise TTSError(f"ElevenLabs returned {exc.status_code}: {exc.body}", exc.status_code,
                           float(retry_after) if retry_after and retry_after.isdigit() else None) from exc

        cost = headers.get("character-cost") or headers.get("x-character-count")
        return TTSResult(audio=audio, request_id=headers.get("request-id", ""),
                         character_cost=int(cost) if cost and cost.isdigit() else None)


class MockTTS:
    """Free stand-in: returns a soft tone whose length matches the text (~15 chars/sec).

    MOCK_FAIL="N:K" makes the call after N successful calls fail K times with a
    503, to exercise retries, the failed-chunk message and resume.
    """

    name = "mock"
    CHARS_PER_SECOND = 15

    def __init__(self, delay: float = 0.4, fail: str | None = None):
        self.delay = delay
        self.calls: list[dict] = []
        self.successes = 0
        self.fail_after, self.fail_times = (int(x) for x in fail.split(":")) if fail else (-1, 0)

    def list_voices(self) -> list[dict]:
        return [
            {"voice_id": "mock-narrator", "name": "Mock Narrator (tone)", "category": "premade", "preview_url": None, "labels": {}},
            {"voice_id": "mock-alt", "name": "Mock Alt (lower tone)", "category": "premade", "preview_url": None, "labels": {}},
        ]

    def clone_voice(self, name: str, files: list[tuple[str, bytes]], description: str | None = None) -> str:
        return f"mock-clone-{uuid.uuid4().hex[:6]}"

    def create_dictionary(self, name: str, rules: list) -> dict:
        return {"pronunciation_dictionary_id": "mock-dict", "version_id": "mock-v1"}

    def synthesize(self, text: str, voice_id: str, model_id: str = MODEL_ID, **kwargs) -> TTSResult:
        self.calls.append({"text": text, "voice_id": voice_id, "model_id": model_id, **kwargs})
        if self.successes == self.fail_after and self.fail_times > 0:
            self.fail_times -= 1
            raise TTSError("Mock 503: service unavailable", 503)
        time.sleep(self.delay)
        seconds = max(1.0, len(text) / self.CHARS_PER_SECOND)
        freq = 165 if voice_id == "mock-alt" else 220
        audio = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds:.2f}",
             "-af", "tremolo=f=3:d=0.7,volume=0.25", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", "-"],
            check=True, capture_output=True,
        ).stdout
        self.successes += 1
        return TTSResult(audio=audio, request_id=f"mock-{uuid.uuid4().hex[:12]}", character_cost=len(text))


def make_tts(mock: bool) -> ElevenLabsTTS | MockTTS:
    return MockTTS(fail=os.getenv("MOCK_FAIL")) if mock else ElevenLabsTTS()
