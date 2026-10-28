"""Every exchange, her voice clip and the memory file, copied to S3.

Why it exists: the owner asked (2026-10-29) for all conversations, the
memory and her voice to be kept in AWS, laid out so a caregiver or family
web portal can be built on them later. The device stays the source of
truth (SPEC.md: the identity file lives where the device is); the bucket
is a copy the portal reads.

The bucket, per device (`<device>` is a random id made once and kept in
`<data dir>/device-id`):

    devices/<device>/transcripts/<YYYY-MM-DD>/<time>-<turn>.json
        {"device_id", "turn_id", "ts", "heard", "reply", "audio_key"}
    devices/<device>/audio/<YYYY-MM-DD>/<time>-<turn>.wav
        what the transcriber was given, 16 kHz mono
    devices/<device>/memory/identity-latest.sqlite3
    devices/<device>/memory/daily/identity-<YYYY-MM-DD>.sqlite3
        a consistent copy (sqlite's backup API), at most every
        MEMORY_EVERY_SECONDS, once the turn's episode has been written

One object per exchange, not an appended log: S3 has no append, and a
portal lists a day by prefix. Each exchange is also appended to
`<data dir>/transcripts/<YYYY-MM-DD>.jsonl` on the device, so the
conversation is kept there even while the network is down.

Nothing here runs on the turn's path: `record_turn` only queues. One
worker thread writes each upload to `<data dir>/cloud/outbox` first and
deletes it once S3 has it, so an upload missed offline or across a
restart is sent later, in order. Lost: uploading from the loop (a slow
network would delay her reply), and a message bus or a vendor SDK
(CLAUDE.md; s3.py says why not boto3).
"""

from __future__ import annotations

import datetime
import json
import logging
import queue
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from saathi.cloud.s3 import S3Target, put_object

logger = logging.getLogger(__name__)

MEMORY_EVERY_SECONDS = 300
# The episode for an exchange is written after the reply (identity/digest.py);
# the snapshot waits this long after the last turn so it includes it.
MEMORY_SETTLE_SECONDS = 20
RETRY_MAX_SECONDS = 300
IDLE_CHECK_SECONDS = 15


def device_id(data_dir: Path) -> str:
    path = Path(data_dir) / "device-id"
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    made = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(made + "\n")
    return made


def _wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(sample_rate)
        clip.writeframes(pcm)
    return buffer.getvalue()


class CloudSync:
    def __init__(
        self,
        data_dir: Path,
        target: S3Target,
        store_path: Path | None,
        *,
        put: Callable[[S3Target, str, bytes, str], None] = put_object,
        clock: Callable[[], datetime.datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.target = target
        self.store_path = Path(store_path) if store_path else None
        self.device = device_id(self.data_dir)
        self.outbox = self.data_dir / "cloud" / "outbox"
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.transcripts = self.data_dir / "transcripts"
        self._put = put
        self._clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self._monotonic = monotonic
        self._jobs: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._memory_dirty_at: float | None = None
        self._memory_sent_at: float | None = None
        self._failures = 0
        self._retry_at = 0.0
        self.uploaded = 0
        self.last_upload: str | None = None
        self.last_error: str | None = None

    # -- the turn's side: queue and return ------------------------------

    def record_turn(self, heard: str, reply: str, audio: bytes | None = None) -> None:
        self._jobs.put((self._clock(), heard, reply, audio))
        self._wake.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="cloud-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def status(self) -> str:
        waiting = len(list(self.outbox.glob("*.json")))
        line = f"AWS S3 bucket {self.target.bucket} ({self.target.region}), device {self.device[:8]}"
        line += f": {self.uploaded} uploaded, {waiting} waiting"
        if self.last_upload:
            line += f", last at {self.last_upload}"
        if self.last_error:
            line += f"; last error: {self.last_error}"
        return line

    # -- the worker -----------------------------------------------------

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(IDLE_CHECK_SECONDS)
            self._wake.clear()
            try:
                self.step()
            except Exception:
                logger.exception("cloud sync step failed")

    def step(self) -> None:
        """One pass: spool what was queued, snapshot the memory if it is
        due, then upload the outbox in order. Public so tests drive it
        without a thread."""
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            self._spool_turn(*job)
            self._memory_dirty_at = self._monotonic()
        self._maybe_snapshot_memory()
        self._upload_outbox()

    def _key(self, kind: str, when: datetime.datetime, name: str) -> str:
        return f"devices/{self.device}/{kind}/{when.strftime('%Y-%m-%d')}/{name}"

    def _spool_turn(self, when: datetime.datetime, heard: str, reply: str, audio: bytes | None) -> None:
        turn = uuid.uuid4().hex[:12]
        stamp = when.strftime("%Y%m%dT%H%M%S") + f"{when.microsecond // 1000:03d}Z"
        audio_key = self._key("audio", when, f"{stamp}-{turn}.wav") if audio else None
        record = {
            "device_id": self.device,
            "turn_id": turn,
            "ts": when.isoformat(timespec="milliseconds"),
            "heard": heard,
            "reply": reply,
            "audio_key": audio_key,
        }
        line = json.dumps(record, ensure_ascii=False)
        self.transcripts.mkdir(parents=True, exist_ok=True)
        with open(self.transcripts / f"{when.strftime('%Y-%m-%d')}.jsonl", "a", encoding="utf-8") as log:
            log.write(line + "\n")
        if audio:
            self._spool(audio_key, _wav(audio), "audio/wav")
        self._spool(self._key("transcripts", when, f"{stamp}-{turn}.json"), line.encode(), "application/json")

    def _spool(self, key: str, body: bytes, content_type: str) -> None:
        """Body first, then its label: a label with no body is never left
        by a crash, and the uploader only sees labelled bodies."""
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex[:6]}"
        (self.outbox / f"{name}.bin").write_bytes(body)
        label = self.outbox / f"{name}.json.tmp"
        label.write_text(json.dumps({"key": key, "content_type": content_type}))
        label.rename(self.outbox / f"{name}.json")

    def _maybe_snapshot_memory(self) -> None:
        if self.store_path is None or self._memory_dirty_at is None:
            return
        now = self._monotonic()
        if now - self._memory_dirty_at < MEMORY_SETTLE_SECONDS:
            return
        if self._memory_sent_at is not None and now - self._memory_sent_at < MEMORY_EVERY_SECONDS:
            return
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "identity.sqlite3"
            source = sqlite3.connect(f"file:{self.store_path}?mode=ro", uri=True)
            target = sqlite3.connect(copy)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            body = copy.read_bytes()
        today = self._clock().strftime("%Y-%m-%d")
        content_type = "application/vnd.sqlite3"
        self._spool(f"devices/{self.device}/memory/identity-latest.sqlite3", body, content_type)
        self._spool(f"devices/{self.device}/memory/daily/identity-{today}.sqlite3", body, content_type)
        self._memory_sent_at = now
        self._memory_dirty_at = None

    def _upload_outbox(self) -> None:
        if self._monotonic() < self._retry_at:
            return
        for label in sorted(self.outbox.glob("*.json")):
            if self._stopping.is_set():
                return
            body_path = label.with_suffix(".bin")
            meta = json.loads(label.read_text())
            try:
                self._put(self.target, meta["key"], body_path.read_bytes(), meta["content_type"])
            except Exception as exc:
                self._failures += 1
                wait = min(RETRY_MAX_SECONDS, 2 ** min(self._failures, 9))
                self._retry_at = self._monotonic() + wait
                self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("cloud upload of %s failed (%s); retrying in %ds", meta["key"], self.last_error, wait)
                return
            body_path.unlink(missing_ok=True)
            label.unlink(missing_ok=True)
            self._failures = 0
            self.uploaded += 1
            self.last_upload = time.strftime("%H:%M:%S")
            self.last_error = None
