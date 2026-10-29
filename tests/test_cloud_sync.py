"""cloud/sync.py: what lands in Firebase, under which paths, and what
happens offline. No network: the client is a fake that records or fails.

Moved from the S3 uploader on 2026-10-29, when the owner chose Firebase
over AWS (DECISIONS.md): the same five behaviours, against Firestore
documents and Storage objects instead of bucket keys."""

import datetime
import io
import json
import wave
from types import SimpleNamespace

import pytest

from saathi.cloud import sync as sync_module
from saathi.cloud.firebase import Account, FirebaseError
from saathi.cloud.sync import CloudSync, device_id
from saathi.identity.preferences import LANGUAGE_KEY, write_preference
from saathi.identity.store import IdentityStore

WHEN = datetime.datetime(2026, 10, 29, 2, 43, 5, 217000, tzinfo=datetime.timezone.utc)


class Project:
    """Firestore and Storage, as far as the device can see them."""

    def __init__(self):
        self.docs = {}
        self.objects = {}
        self.calls = []
        self.down = False
        self.config = SimpleNamespace(project_id="saathi-family")

    def _net(self, what):
        self.calls.append(what)
        if self.down:
            raise OSError("network is unreachable")

    def ensure_device_account(self, data_dir, device):
        self._net(("account", device))
        return Account(uid="uid-" + device[:4], email=f"device-{device}@saathi.invalid")

    def create_doc(self, path, fields, doc_id=None):
        self._net(("create", f"{path}/{doc_id}"))
        if f"{path}/{doc_id}" in self.docs:
            raise FirebaseError(409, "ALREADY_EXISTS")
        self.docs[f"{path}/{doc_id}"] = dict(fields)

    def patch_doc(self, path, fields, update_mask=None):
        self._net(("patch", path))
        if update_mask is None:
            self.docs[path] = dict(fields)
        else:
            self.docs.setdefault(path, {}).update({name: fields[name] for name in update_mask})

    def get_doc(self, path):
        self._net(("get", path))
        return self.docs.get(path)

    def upload(self, object_path, body, content_type):
        self._net(("upload", object_path))
        self.objects[object_path] = (body, content_type)

    def turns(self):
        return {path: doc for path, doc in self.docs.items() if "/turns/" in path}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def store_path(tmp_path):
    path = tmp_path / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
        store.append(
            "episodes",
            ts=WHEN.isoformat(),
            entity_id=None,
            text="She asked for a song.",
            importance=0.4,
            embedding=None,
        )
    return path


def _sync(tmp_path, store_path, project, clock):
    return CloudSync(tmp_path, project, store_path, clock=lambda: WHEN, monotonic=clock)


def test_an_exchange_lands_as_a_turn_document_and_its_clip_under_the_device(tmp_path, store_path):
    project, clock = Project(), Clock()
    cloud = _sync(tmp_path, store_path, project, clock)
    cloud.record_turn("Ve Kangna tera ni", "Playing it now.", audio=b"\x10\x00" * 160)
    cloud.step()
    device = device_id(tmp_path)
    turns = project.turns()
    assert len(turns) == 1 and len(project.objects) == 1
    [(path, doc)] = turns.items()
    assert path.startswith(f"devices/{device}/turns/20261029T024305217Z-")
    turn = path.rsplit("/", 1)[1]
    clip = f"devices/{device}/audio/{turn}.wav"
    assert doc == {
        "ts": WHEN,
        "heard": "Ve Kangna tera ni",
        "said": "Playing it now.",
        "language": "english",  # the default: nothing set
        "clipPath": clip,
    }
    body, content_type = project.objects[clip]
    assert content_type == "audio/wav"
    with wave.open(io.BytesIO(body)) as audio:
        assert audio.getframerate() == 16000 and audio.readframes(160) == b"\x10\x00" * 160
    # The clip goes up before the turn that points at it.
    assert project.calls.index(("upload", clip)) < project.calls.index(("patch", path))
    # Kept on the device too, whatever the network does.
    local = (tmp_path / "transcripts" / "2026-10-29.jsonl").read_text().splitlines()
    assert [json.loads(line)["turn_id"] for line in local] == [turn]
    assert list(cloud.outbox.iterdir()) == []


def test_the_device_document_is_made_once_with_its_uid_and_never_overwritten(tmp_path, store_path):
    project, clock = Project(), Clock()
    device = device_id(tmp_path)
    cloud = _sync(tmp_path, store_path, project, clock)
    cloud.record_turn("hi", "hello")
    cloud.step()
    assert project.docs[f"devices/{device}"] == {"deviceUid": "uid-" + device[:4], "caregivers": []}
    assert "clipPath" not in next(iter(project.turns().values()))  # no clip, no path
    # Pairing adds a caregiver; a restart must not wipe it.
    project.docs[f"devices/{device}"]["caregivers"] = ["caregiver-uid"]
    again = _sync(tmp_path, store_path, project, clock)
    again.record_turn("again", "yes")
    again.step()
    assert project.docs[f"devices/{device}"]["caregivers"] == ["caregiver-uid"]
    assert len(project.turns()) == 2 and again.last_error is None


def test_the_language_is_the_one_she_is_spoken_to_in(tmp_path, store_path):
    with IdentityStore(store_path) as store:
        write_preference(store, LANGUAGE_KEY, "hindi")
    project, clock = Project(), Clock()
    cloud = _sync(tmp_path, store_path, project, clock)
    cloud.record_turn("namaste", "namaste ji")
    cloud.record_turn("hello", "hello", language="english")
    cloud.step()
    assert sorted(doc["language"] for doc in project.turns().values()) == ["english", "hindi"]


def test_offline_everything_waits_in_order_and_goes_up_when_back(tmp_path, store_path):
    project, clock = Project(), Clock()
    project.down = True
    cloud = _sync(tmp_path, store_path, project, clock)
    cloud.record_turn("one", "first", audio=None)
    cloud.record_turn("two", "second", audio=None)
    cloud.step()
    assert project.docs == {} and "network is unreachable" in cloud.last_error
    # A restart in between: a new CloudSync finds the outbox.
    project.down = False
    again = _sync(tmp_path, store_path, project, clock)
    again.step()
    patched = [what[1] for what in project.calls if what[0] == "patch"]
    assert [project.docs[path]["heard"] for path in patched] == ["one", "two"]
    assert again.uploaded == 2 and again.last_error is None


def test_a_failure_backs_off_instead_of_hammering(tmp_path):
    project, clock = Project(), Clock()
    project.down = True
    cloud = _sync(tmp_path, None, project, clock)  # no memory file: uploads are the exchange's
    cloud.record_turn("hi", "hello", audio=None)
    cloud.step()
    cloud.step()  # still inside the back-off: no second try
    assert len(project.calls) == 1
    clock.now += sync_module.RETRY_MAX_SECONDS
    project.down = False
    cloud.step()
    assert ("patch", next(iter(project.turns()))) in project.calls and cloud.uploaded == 1
    assert next(iter(project.turns().values()))["language"] is None  # no store, no preference


def test_a_doc_refused_by_firebase_waits_like_an_offline_one(tmp_path):
    project, clock = Project(), Clock()
    refused = FirebaseError(403, "PERMISSION_DENIED")
    project.patch_doc = lambda *a, **k: (_ for _ in ()).throw(refused)
    cloud = _sync(tmp_path, None, project, clock)
    cloud.record_turn("hi", "hello")
    cloud.step()
    assert "PERMISSION_DENIED" in cloud.last_error and len(list(cloud.outbox.glob("*.json"))) == 1
    assert "PERMISSION_DENIED" in cloud.status()


def test_the_memory_file_follows_after_the_episode_settles_at_most_every_five_minutes(
    tmp_path, store_path
):
    project, clock = Project(), Clock()
    cloud = _sync(tmp_path, store_path, project, clock)
    device = device_id(tmp_path)
    cloud.record_turn("hi", "hello")
    cloud.step()
    assert not [k for k in project.objects if "/memory/" in k]  # the episode isn't written yet
    clock.now += sync_module.MEMORY_SETTLE_SECONDS
    cloud.step()
    snapshot = f"devices/{device}/memory/20261029T024305Z.sqlite3"
    assert list(project.objects) == [snapshot]
    body, content_type = project.objects[snapshot]
    assert content_type == "application/vnd.sqlite3"
    copy = tmp_path / "copy.sqlite3"
    copy.write_bytes(body)
    with IdentityStore(copy) as store:  # a whole, readable database
        assert [e["text"] for e in store.read("episodes")] == ["She asked for a song."]
    del project.objects[snapshot]
    cloud.record_turn("again", "yes")
    clock.now += sync_module.MEMORY_SETTLE_SECONDS
    cloud.step()
    assert snapshot not in project.objects  # within five minutes of the last copy
    clock.now += sync_module.MEMORY_EVERY_SECONDS
    cloud.step()
    assert snapshot in project.objects


def test_an_item_left_by_the_s3_uploader_is_dropped_with_a_log_line(tmp_path, caplog):
    project, clock = Project(), Clock()
    project.down = True  # dropped even offline: it is never going anywhere
    cloud = _sync(tmp_path, None, project, clock)
    (cloud.outbox / "00000000000000000001-abcdef.bin").write_bytes(b"{}")
    (cloud.outbox / "00000000000000000001-abcdef.json").write_text(
        json.dumps(
            {"key": "devices/d/transcripts/2026-10-29/x.json", "content_type": "application/json"}
        )
    )
    with caplog.at_level("INFO", logger="saathi.cloud.sync"):
        cloud.step()
    assert list(cloud.outbox.iterdir()) == [] and project.calls == []
    assert "devices/d/transcripts/2026-10-29/x.json" in caplog.text and "S3" in caplog.text


def test_a_device_signed_up_again_takes_its_document_back_keeping_the_caregivers(tmp_path):
    # Its credential file was lost and the old user deleted in the
    # console: the rules key on deviceUid, so it must follow the new uid.
    project, clock = Project(), Clock()
    device = device_id(tmp_path)
    project.docs[f"devices/{device}"] = {"deviceUid": "old-uid", "caregivers": ["caregiver-uid"]}
    cloud = _sync(tmp_path, None, project, clock)
    cloud.record_turn("hi", "hello")
    cloud.step()
    assert project.docs[f"devices/{device}"] == {
        "deviceUid": "uid-" + device[:4],
        "caregivers": ["caregiver-uid"],
    }
    assert cloud.last_error is None and len(project.turns()) == 1


def test_an_unreadable_label_is_set_aside_and_does_not_hold_up_the_rest(tmp_path):
    project, clock = Project(), Clock()
    cloud = _sync(tmp_path, None, project, clock)
    (cloud.outbox / "00000000000000000001-abcdef.bin").write_bytes(b"x")
    (cloud.outbox / "00000000000000000001-abcdef.json").write_text('{"kind": "doc", "pa')
    cloud.record_turn("hi", "hello")
    cloud.step()
    assert len(project.turns()) == 1 and cloud.last_error is None
    assert (cloud.outbox / "00000000000000000001-abcdef.json.bad").exists()


def test_the_days_file_on_the_device_keeps_its_field_names(tmp_path):
    project, clock = Project(), Clock()
    cloud = _sync(tmp_path, None, project, clock)
    cloud.record_turn("hi", "hello", audio=b"\x00\x00", language="hindi")
    cloud.step()
    [line] = (tmp_path / "transcripts" / "2026-10-29.jsonl").read_text().splitlines()
    record = json.loads(line)
    assert record["reply"] == "hello" and record["language"] == "hindi"
    assert record["audio_key"] in project.objects


def test_the_device_id_is_made_once_and_kept(tmp_path):
    first = device_id(tmp_path)
    assert len(first) == 32 and device_id(tmp_path) == first
