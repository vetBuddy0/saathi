"""cloud/sync.py: what lands in the bucket, under which keys, and what
happens offline. No network: `put` is a fake that records or fails."""

import datetime
import io
import json
import wave

import pytest

from saathi.cloud import sync as sync_module
from saathi.cloud.s3 import S3Target
from saathi.cloud.sync import CloudSync, device_id
from saathi.identity.store import IdentityStore

TARGET = S3Target("AKIA1", "secret", "ap-southeast-1", "saathi-data")
WHEN = datetime.datetime(2026, 10, 29, 2, 43, 5, 217000, tzinfo=datetime.timezone.utc)


class Bucket:
    def __init__(self):
        self.objects = {}
        self.down = False

    def put(self, target, key, body, content_type):
        if self.down:
            raise OSError("network is unreachable")
        self.objects[key] = (body, content_type)


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
        store.append("episodes", ts=WHEN.isoformat(), entity_id=None, text="She asked for a song.",
                     importance=0.4, embedding=None)
    return path


def _sync(tmp_path, store_path, bucket, clock):
    return CloudSync(tmp_path, TARGET, store_path, put=bucket.put, clock=lambda: WHEN, monotonic=clock)


def test_an_exchange_lands_as_a_transcript_and_its_clip_under_the_device(tmp_path, store_path):
    bucket, clock = Bucket(), Clock()
    cloud = _sync(tmp_path, store_path, bucket, clock)
    cloud.record_turn("Ve Kangna tera ni", "Playing it now.", audio=b"\x10\x00" * 160)
    cloud.step()
    device = device_id(tmp_path)
    transcripts = [k for k in bucket.objects if "/transcripts/" in k]
    clips = [k for k in bucket.objects if "/audio/" in k]
    assert len(transcripts) == 1 and len(clips) == 1
    assert transcripts[0].startswith(f"devices/{device}/transcripts/2026-10-29/20261029T024305217Z-")
    body, content_type = bucket.objects[transcripts[0]]
    record = json.loads(body)
    assert content_type == "application/json"
    assert record["heard"] == "Ve Kangna tera ni" and record["reply"] == "Playing it now."
    assert record["device_id"] == device and record["audio_key"] == clips[0]
    assert record["ts"] == "2026-10-29T02:43:05.217+00:00"
    with wave.open(io.BytesIO(bucket.objects[clips[0]][0])) as clip:
        assert clip.getframerate() == 16000 and clip.readframes(160) == b"\x10\x00" * 160
    # Kept on the device too, whatever the network does.
    local = (tmp_path / "transcripts" / "2026-10-29.jsonl").read_text().splitlines()
    assert [json.loads(line)["turn_id"] for line in local] == [record["turn_id"]]
    assert list(cloud.outbox.iterdir()) == []


def test_offline_everything_waits_in_order_and_goes_up_when_back(tmp_path, store_path):
    bucket, clock = Bucket(), Clock()
    bucket.down = True
    cloud = _sync(tmp_path, store_path, bucket, clock)
    cloud.record_turn("one", "first", audio=None)
    cloud.record_turn("two", "second", audio=None)
    cloud.step()
    assert bucket.objects == {} and "network is unreachable" in cloud.last_error
    # A restart in between: a new CloudSync finds the outbox.
    bucket.down = False
    again = _sync(tmp_path, store_path, bucket, clock)
    again.step()
    heard = [json.loads(body)["heard"] for key, (body, _) in sorted(bucket.objects.items())
             if "/transcripts/" in key]
    assert sorted(heard) == ["one", "two"] and again.uploaded == 2
    assert again.last_error is None


def test_a_failure_backs_off_instead_of_hammering(tmp_path):
    bucket, clock = Bucket(), Clock()
    bucket.down = True
    calls = []
    original = bucket.put
    bucket.put = lambda *a: (calls.append(a[1]), original(*a))
    cloud = _sync(tmp_path, None, bucket, clock)  # no memory file: uploads are the exchange's
    cloud.record_turn("hi", "hello", audio=None)
    cloud.step()
    cloud.step()  # still inside the back-off: no second try
    assert len(calls) == 1
    clock.now += sync_module.RETRY_MAX_SECONDS
    bucket.down = False
    cloud.step()
    assert len(calls) == 2 and cloud.uploaded == 1


def test_the_memory_file_follows_after_the_episode_settles_at_most_every_five_minutes(
    tmp_path, store_path
):
    bucket, clock = Bucket(), Clock()
    cloud = _sync(tmp_path, store_path, bucket, clock)
    device = device_id(tmp_path)
    cloud.record_turn("hi", "hello")
    cloud.step()
    assert not [k for k in bucket.objects if "/memory/" in k]  # the episode isn't written yet
    clock.now += sync_module.MEMORY_SETTLE_SECONDS
    cloud.step()
    latest = f"devices/{device}/memory/identity-latest.sqlite3"
    daily = f"devices/{device}/memory/daily/identity-2026-10-29.sqlite3"
    assert latest in bucket.objects and daily in bucket.objects
    copy = tmp_path / "copy.sqlite3"
    copy.write_bytes(bucket.objects[latest][0])
    with IdentityStore(copy) as store:  # a whole, readable database
        assert [e["text"] for e in store.read("episodes")] == ["She asked for a song."]
    del bucket.objects[latest]
    cloud.record_turn("again", "yes")
    clock.now += sync_module.MEMORY_SETTLE_SECONDS
    cloud.step()
    assert latest not in bucket.objects  # within five minutes of the last copy
    clock.now += sync_module.MEMORY_EVERY_SECONDS
    cloud.step()
    assert latest in bucket.objects


def test_the_device_id_is_made_once_and_kept(tmp_path):
    first = device_id(tmp_path)
    assert len(first) == 32 and device_id(tmp_path) == first
