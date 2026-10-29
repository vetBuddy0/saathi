"""cloud/firebase.py and cloud/sync.py against the real Firebase
emulators: a device signs itself up, an exchange and its clip go up
through `CloudSync.step()`, and everything is read back.

Skipped unless the emulators are running (`firebase emulators:exec`
sets FIRESTORE_EMULATOR_HOST and the others). The emulators take any API
key; the project and bucket come from the environment when set, else
demo names. Run with open rules (the real ones live in contracts/):

    firebase emulators:exec --only auth,firestore,storage --project demo-saathi \\
        "env PYTHONPATH= python -m pytest -q -p no:cacheprovider \\
         tests/test_cloud_firebase_emulator.py"
"""

import datetime
import io
import os
import wave

import pytest

from saathi.cloud import sync as sync_module
from saathi.cloud.firebase import FirebaseClient, FirebaseConfig
from saathi.cloud.sync import CloudSync, device_id
from saathi.identity.store import IdentityStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firebase emulators (FIRESTORE_EMULATOR_HOST)",
)


def _config() -> FirebaseConfig:
    project = os.environ.get("GCLOUD_PROJECT", "demo-saathi")
    env = {
        "FIREBASE_API_KEY": "demo-key",
        "FIREBASE_PROJECT_ID": project,
        "FIREBASE_STORAGE_BUCKET": f"{project}.appspot.com",
        **os.environ,
    }
    config = FirebaseConfig.from_env(env)
    assert config is not None and config.auth_emulator and config.storage_emulator
    return config


WHEN = datetime.datetime(2026, 10, 29, 2, 43, 5, 217000, tzinfo=datetime.timezone.utc)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_device_signs_up_and_its_exchange_clip_and_memory_are_readable_back(tmp_path):
    store_path = tmp_path / "identity.sqlite3"
    with IdentityStore(store_path) as store:
        store.create()
        store.append(
            "episodes",
            ts="2026-10-29T02:43:05+00:00",
            entity_id=None,
            text="She asked for a song.",
            importance=0.4,
            embedding=None,
        )
    clock = Clock()
    client = FirebaseClient(_config())
    cloud = CloudSync(tmp_path, client, store_path, clock=lambda: WHEN, monotonic=clock)
    device = device_id(tmp_path)
    pcm = b"\x10\x00" * 1600

    cloud.record_turn("Ve Kangna tera ni", "Playing it now.", audio=pcm)
    cloud.step()
    assert cloud.last_error is None and cloud.uploaded == 2
    assert list(cloud.outbox.iterdir()) == []

    home = client.get_doc(f"devices/{device}")
    assert home == {"id": device, "deviceUid": client.account.uid, "caregivers": []}
    assert client.account.email == f"device-{device}@saathi.invalid"

    [turn] = client.query(f"devices/{device}", "turns", order_by="ts", limit=10)
    assert turn["heard"] == "Ve Kangna tera ni" and turn["said"] == "Playing it now."
    assert turn["language"] == "english"
    assert turn["ts"] == WHEN
    assert turn["clipPath"] == f"devices/{device}/audio/{turn.id}.wav"
    found = client.query(f"devices/{device}", "turns", where=[("heard", "==", "Ve Kangna tera ni")])
    assert [doc.id for doc in found] == [turn.id]

    with wave.open(io.BytesIO(client.download(turn["clipPath"]))) as clip:
        assert clip.getframerate() == 16000 and clip.readframes(1600) == pcm

    clock.now += sync_module.MEMORY_SETTLE_SECONDS
    cloud.step()
    assert cloud.last_error is None and cloud.uploaded == 3
    memory = tmp_path / "memory-back.sqlite3"
    memory.write_bytes(client.download(f"devices/{device}/memory/20261029T024305Z.sqlite3"))
    with IdentityStore(memory) as copy:  # a whole, readable database
        assert [e["text"] for e in copy.read("episodes")] == ["She asked for a song."]

    # A restart signs in (not up) as the same user, and the device
    # document is left as pairing would have left it.
    client.patch_doc(
        f"devices/{device}", {"caregivers": ["caregiver-uid"]}, update_mask=["caregivers"]
    )
    again = FirebaseClient(_config())
    restarted = CloudSync(tmp_path, again, store_path, clock=lambda: WHEN, monotonic=clock)
    restarted.record_turn("again", "yes")
    restarted.step()
    assert restarted.last_error is None and again.account.uid == client.account.uid
    assert again.get_doc(f"devices/{device}")["caregivers"] == ["caregiver-uid"]
    assert len(again.query(f"devices/{device}", "turns")) == 2
