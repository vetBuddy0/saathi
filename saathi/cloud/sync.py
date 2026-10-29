"""Every exchange, her voice clip and the memory file, copied to Firebase.

Why it exists: the owner asked (2026-10-29) for all conversations, the
memory and her voice to be kept in the cloud, laid out so a caregiver or
family app can be built on them; the same day he chose Firebase for all
of it, not AWS (DECISIONS.md). The device stays the source of truth
(SPEC.md: the identity file lives where the device is); the Firebase
project is a copy the caregiver app reads.

The layout, per device (`<device>` is a random id made once and kept in
`<data dir>/device-id`; `<turn>` is `<UTC time>-<random>`, so ids sort
in time order):

    Firestore devices/<device>
        {deviceUid, caregivers: []} -- made on the first upload, never
        overwritten (pairing adds caregivers)
    Firestore devices/<device>/turns/<turn>
        {ts, heard, said, language, clipPath?}
    Storage   devices/<device>/audio/<turn>.wav
        what the transcriber was given, 16 kHz mono
    Storage   devices/<device>/memory/<UTC time>.sqlite3
        a consistent copy (sqlite's backup API), at most every
        MEMORY_EVERY_SECONDS, once the turn's episode has been written

The device signs in as its own Firebase user (firebase.py's
`ensure_device_account`); `deviceUid` is that user's uid, so the rules
can say only this device writes under `devices/<device>`. Each exchange
is also appended to `<data dir>/transcripts/<YYYY-MM-DD>.jsonl` on the
device, so the conversation is kept there even while the network is down.

Nothing here runs on the turn's path: `record_turn` only queues. One
worker thread writes each upload to `<data dir>/cloud/outbox` first and
deletes it once Firebase has it, so an upload missed offline or across a
restart is sent later, in order -- a clip before the turn that points at
it. A turn is written with a whole-document PATCH, so a retry after a
lost reply rewrites the same document instead of failing on "exists".
An outbox item left by the S3 uploader this replaced has no `kind`; it
is dropped with a log line (no AWS keys were ever entered, so in
practice there are none).

Lost: uploading from the loop (a slow network would delay her reply),
and a message bus or a vendor SDK (CLAUDE.md; firebase.py says why not
firebase-admin). Also lost: creating the turn document with
`documentId` (a retry after a lost reply would get ALREADY_EXISTS and
need its own special case; PATCH is idempotent).
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
from typing import Any

from saathi.cloud.firebase import (
    Account,
    AlreadyExists,
    FirebaseClient,
    decode_fields,
    encode_fields,
)

logger = logging.getLogger(__name__)

MEMORY_EVERY_SECONDS = 300
# The episode for an exchange is written after the reply (identity/digest.py);
# the snapshot waits this long after the last turn so it includes it.
MEMORY_SETTLE_SECONDS = 20
RETRY_MAX_SECONDS = 300
IDLE_CHECK_SECONDS = 15
KINDS = ("doc", "object")


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
    """`client` is a `FirebaseClient`, or anything with its
    `ensure_device_account`, `create_doc`, `patch_doc` and `upload`
    (tests pass a fake)."""

    def __init__(
        self,
        data_dir: Path,
        client: FirebaseClient | Any,
        store_path: Path | None,
        *,
        clock: Callable[[], datetime.datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.client = client
        self.store_path = Path(store_path) if store_path else None
        self.device = device_id(self.data_dir)
        self.outbox = self.data_dir / "cloud" / "outbox"
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.transcripts = self.data_dir / "transcripts"
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
        self.account: Account | None = None
        self._device_doc_made = False
        self.uploaded = 0
        self.last_upload: str | None = None
        self.last_error: str | None = None

    # -- the turn's side: queue and return ------------------------------

    def record_turn(
        self, heard: str, reply: str, audio: bytes | None = None, language: str | None = None
    ) -> None:
        """`language` defaults to the device's `language` preference,
        read on the worker, not here: no database read on the loop."""
        self._jobs.put((self._clock(), heard, reply, audio, language))
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
        project = getattr(getattr(self.client, "config", None), "project_id", "?")
        line = f"Firebase project {project}, device {self.device[:8]}"
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

    def _language(self) -> str | None:
        if self.store_path is None:
            return None
        from saathi.identity.preferences import LANGUAGE_KEY, read_preference
        from saathi.identity.store import IdentityStore
        from saathi.voice.language import DEFAULT_LANGUAGE

        try:
            with IdentityStore(self.store_path) as store:
                return read_preference(store, LANGUAGE_KEY, DEFAULT_LANGUAGE)
        except Exception:
            logger.warning("cloud sync: could not read the language preference", exc_info=True)
            return None

    def _spool_turn(
        self,
        when: datetime.datetime,
        heard: str,
        reply: str,
        audio: bytes | None,
        language: str | None,
    ) -> None:
        stamp = when.strftime("%Y%m%dT%H%M%S") + f"{when.microsecond // 1000:03d}Z"
        turn = f"{stamp}-{uuid.uuid4().hex[:8]}"
        if language is None:
            language = self._language()
        clip_path = f"devices/{self.device}/audio/{turn}.wav" if audio else None
        fields: dict[str, Any] = {"ts": when, "heard": heard, "said": reply, "language": language}
        if clip_path:
            fields["clipPath"] = clip_path
        # The S3 uploader's field names, plus `language`: a day's file on
        # the device keeps one shape across the switch.
        local = {
            "device_id": self.device,
            "turn_id": turn,
            "ts": when.isoformat(timespec="milliseconds"),
            "heard": heard,
            "reply": reply,
            "audio_key": clip_path,
            "language": language,
        }
        self.transcripts.mkdir(parents=True, exist_ok=True)
        with open(self.transcripts / f"{when.strftime('%Y-%m-%d')}.jsonl", "a", encoding="utf-8") as log:
            log.write(json.dumps(local, ensure_ascii=False) + "\n")
        if clip_path:
            label = {"kind": "object", "path": clip_path, "content_type": "audio/wav"}
            self._spool(label, _wav(audio or b""))
        # Fields in Firestore's own encoding, so the timestamp survives
        # the trip through a JSON file.
        body = json.dumps(encode_fields(fields), ensure_ascii=False).encode()
        self._spool({"kind": "doc", "path": f"devices/{self.device}/turns/{turn}"}, body)

    def _spool(self, label: dict, body: bytes) -> None:
        """Body first, then its label: a label with no body is never left
        by a crash, and the uploader only sees labelled bodies."""
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex[:6]}"
        (self.outbox / f"{name}.bin").write_bytes(body)
        temporary = self.outbox / f"{name}.json.tmp"
        temporary.write_text(json.dumps(label))
        temporary.rename(self.outbox / f"{name}.json")

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
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        path = f"devices/{self.device}/memory/{stamp}.sqlite3"
        label = {"kind": "object", "path": path, "content_type": "application/vnd.sqlite3"}
        self._spool(label, body)
        self._memory_sent_at = now
        self._memory_dirty_at = None

    # -- uploading ------------------------------------------------------

    def _prepare(self) -> None:
        """Signed in as this device, and `devices/<device>` made, once per
        run. Created, never overwritten: by the time it exists, pairing
        may have added caregivers. If it exists with another uid (the
        device's user was deleted and signed up again), only `deviceUid`
        is corrected, so the rules let this user write again; if the
        rules refuse even that, the error shows in the console."""
        if self.account is None:
            self.account = self.client.ensure_device_account(self.data_dir, self.device)
        if self._device_doc_made:
            return
        uid = self.account.uid
        home = f"devices/{self.device}"
        try:
            fields = {"deviceUid": uid, "caregivers": []}
            self.client.create_doc("devices", fields, doc_id=self.device)
        except AlreadyExists:
            existing = self.client.get_doc(home)
            if existing is not None and existing.get("deviceUid") != uid:
                logger.warning("cloud sync: %s named another user; updating deviceUid", home)
                self.client.patch_doc(home, {"deviceUid": uid}, update_mask=["deviceUid"])
        self._device_doc_made = True

    def _send(self, label: dict, body: bytes) -> None:
        if label["kind"] == "doc":
            self.client.patch_doc(label["path"], decode_fields(json.loads(body)))
        else:
            self.client.upload(label["path"], body, label["content_type"])

    def _fail(self, what: str, exc: Exception) -> None:
        self._failures += 1
        wait = min(RETRY_MAX_SECONDS, 2 ** min(self._failures, 9))
        self._retry_at = self._monotonic() + wait
        self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        logger.warning("cloud upload of %s failed (%s); retrying in %ds", what, self.last_error, wait)

    def _upload_outbox(self) -> None:
        if self._monotonic() < self._retry_at:
            return
        pending = []
        for label_path in sorted(self.outbox.glob("*.json")):
            try:
                label = json.loads(label_path.read_text())
            except (OSError, ValueError):
                # Labels are written whole and renamed into place, so this
                # is a damaged disk, not a crash mid-write. Set aside, not
                # deleted, and not allowed to hold up everything after it.
                logger.warning("cloud sync: unreadable outbox label %s set aside", label_path.name)
                label_path.rename(label_path.with_name(label_path.name + ".bad"))
                continue
            if label.get("kind") in KINDS:
                pending.append((label_path, label))
                continue
            logger.info(
                "cloud sync: dropped %s, left by the S3 uploader Firebase replaced",
                label.get("key", label_path.name),
            )
            label_path.with_suffix(".bin").unlink(missing_ok=True)
            label_path.unlink(missing_ok=True)
        if not pending:
            return
        try:
            self._prepare()
        except Exception as exc:
            self._fail("the device's account", exc)
            return
        for label_path, label in pending:
            if self._stopping.is_set():
                return
            body_path = label_path.with_suffix(".bin")
            try:
                self._send(label, body_path.read_bytes())
            except Exception as exc:
                self._fail(label["path"], exc)
                return
            body_path.unlink(missing_ok=True)
            label_path.unlink(missing_ok=True)
            self._failures = 0
            self.uploaded += 1
            self.last_upload = time.strftime("%H:%M:%S")
            self.last_error = None
