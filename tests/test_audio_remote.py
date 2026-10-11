"""audio/remote.py -- the /audio seam, without a server: a fake socket
and the real event loop. What's pinned is the contract the Android
shell is written against (screen/server.py's docstring): frames are
forwarded only while listening; a play is one text header then one
binary WAV; `wait()` returns on the matching `played`, on `stop()`, on
the client leaving, or after the WAV's length plus the grace -- never
later; and with no client the local `play` is used unchanged.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from pathlib import Path

import pytest

from saathi.audio import remote as remote_module
from saathi.audio.remote import (
    DEFAULT_GRACE_SECONDS,
    DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
    RemoteAudio,
    RemoteSynthesisError,
    wav_seconds,
)


class FakeWS:
    """An aiohttp WebSocketResponse as the seam sees it: `send_str`,
    `send_bytes`, `closed`."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.sent.append(("text", json.loads(data)))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(("binary", data))

    async def close(self) -> None:
        self.closed = True


def silence_wav(seconds: float, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


async def _eventually(condition, seconds: float = 2.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.005)


@pytest.fixture
def wav_path(tmp_path) -> Path:
    path = tmp_path / "sentence.wav"
    path.write_bytes(silence_wav(0.05))
    return path


def test_wav_seconds_reads_the_header_and_estimates_when_it_cannot():
    assert wav_seconds(silence_wav(0.5)) == pytest.approx(0.5)
    assert wav_seconds(silence_wav(0.25, rate=22050)) == pytest.approx(0.25, abs=1e-3)
    # Not a WAV at all: 32000 bytes/s, an overestimate for every backend.
    assert wav_seconds(b"\x00" * 64000) == pytest.approx(2.0)


def test_feed_only_forwards_while_listening():
    remote = RemoteAudio()
    heard: list[bytes] = []
    remote.feed(b"before")
    remote.start_listening(heard.append)
    remote.feed(b"during")
    remote.feed(b"during too")
    remote.stop_listening()
    remote.feed(b"after")
    assert heard == [b"during", b"during too"]


async def test_player_sends_the_header_then_the_wav_and_wait_returns_on_played(wav_path):
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    assert not remote.attached
    assert remote.attach(ws, loop) is None
    assert remote.attached

    handle = remote.player("fake-sink", wav_path)
    await _eventually(lambda: len(ws.sent) == 2)
    assert ws.sent[0] == ("text", {"type": "play", "id": handle.id, "format": "wav"})
    assert ws.sent[1] == ("binary", wav_path.read_bytes())
    assert handle.timeout_seconds == pytest.approx(0.05 + DEFAULT_GRACE_SECONDS)

    waiting = loop.run_in_executor(None, handle.wait)  # the say() thread
    await asyncio.sleep(0.05)
    assert not waiting.done() and not handle.finished
    remote.on_played("some-other-id")  # a stale ack ends nothing
    await asyncio.sleep(0.02)
    assert not waiting.done()
    remote.on_played(handle.id)
    await asyncio.wait_for(waiting, 1.0)
    assert handle.finished and handle.acknowledged
    assert len(ws.sent) == 2  # nothing else was sent


async def test_stop_sends_stop_after_the_play_frames_and_releases_the_wait(wav_path):
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(ws, loop)

    handle = remote.player("fake-sink", wav_path)
    waiting = loop.run_in_executor(None, handle.wait)
    # Stop before the loop has even run the send: the frames must still
    # leave in order -- header, WAV, stop -- never stop first.
    handle.stop()
    await asyncio.wait_for(waiting, 1.0)
    assert handle.finished and not handle.acknowledged
    await _eventually(lambda: len(ws.sent) == 3)
    assert [kind for kind, _ in ws.sent] == ["text", "binary", "text"]
    assert ws.sent[2] == ("text", {"type": "stop"})
    # The client answers a stop with `played` for what it cut short:
    # harmless, and a second stop on a finished handle sends nothing.
    remote.on_played(handle.id)
    handle.stop()
    await asyncio.sleep(0.02)
    assert len(ws.sent) == 3


async def test_without_a_client_the_player_falls_back_to_local_play(wav_path):
    local_calls: list[tuple[str, Path]] = []

    class LocalHandle:
        finished = True

        def wait(self) -> None:
            pass

        def stop(self) -> None:
            pass

    def local_play(sink_id: str, path: Path) -> LocalHandle:
        local_calls.append((sink_id, path))
        return LocalHandle()

    remote = RemoteAudio(fallback=local_play)
    handle = remote.player("fake-sink", wav_path)
    assert isinstance(handle, LocalHandle)
    assert local_calls == [("fake-sink", wav_path)]

    # Attached then detached: local again, and a closed socket counts as gone.
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote.attach(ws, loop)
    remote.detach(ws)
    assert not remote.attached
    remote.player("fake-sink", wav_path)
    assert len(local_calls) == 2
    remote.attach(ws, loop)
    ws.closed = True
    assert not remote.attached
    remote.player("fake-sink", wav_path)
    assert len(local_calls) == 3 and ws.sent == []


def test_the_default_fallback_is_the_local_play():
    assert RemoteAudio()._fallback is remote_module.play


async def test_wait_gives_up_after_the_wav_duration_plus_the_grace(wav_path, caplog):
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local, grace_seconds=0.1)
    remote.attach(ws, loop)

    handle = remote.player("fake-sink", wav_path)
    assert handle.timeout_seconds == pytest.approx(0.15)
    started = time.monotonic()
    await asyncio.wait_for(loop.run_in_executor(None, handle.wait), 1.0)
    elapsed = time.monotonic() - started
    assert 0.1 <= elapsed < 0.8
    assert handle.finished and not handle.acknowledged
    assert "no 'played' for " + handle.id in caplog.text
    # A late ack is ignored, and the next play gets a fresh id.
    remote.on_played(handle.id)
    later = remote.player("fake-sink", wav_path)
    assert later.id != handle.id


async def test_detaching_the_client_mid_play_releases_the_wait(wav_path):
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(ws, loop)
    handle = remote.player("fake-sink", wav_path)
    waiting = loop.run_in_executor(None, handle.wait)
    await asyncio.sleep(0.02)
    assert not waiting.done()
    remote.detach(ws)
    await asyncio.wait_for(waiting, 1.0)
    assert handle.finished and not handle.acknowledged


async def test_a_new_client_replaces_the_old_and_a_stale_detach_is_ignored(wav_path):
    loop = asyncio.get_running_loop()
    first, second = FakeWS(), FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(first, loop)
    assert remote.attach(second, loop) is first  # for the server to close
    remote.detach(first)  # the replaced connection's own close, late
    assert remote.attached
    handle = remote.player("fake-sink", wav_path)
    await _eventually(lambda: len(second.sent) == 2)
    assert first.sent == []
    remote.on_played(handle.id)
    assert handle.finished


def _never_local(sink_id: str, path: Path):
    raise AssertionError("local playback must not be used while a client is attached")


def test_the_grace_is_the_contracts_three_seconds():
    # "a play waits at most (wav duration + 3 s)" -- the number itself,
    # so a change to the constant fails a test rather than every test
    # measuring itself against it.
    assert DEFAULT_GRACE_SECONDS == 3.0


async def test_replacing_the_client_mid_play_releases_the_wait(wav_path):
    # The app restarted (or a Wi-Fi blip reconnected it) mid-sentence: the
    # new connection replaces the old, and the old one's close is then a
    # stale detach that changes nothing -- so the replacement itself must
    # release the play, or the say() thread waits the WAV plus the grace
    # with her face held on "speaking". The sentence is lost, not re-sent.
    loop = asyncio.get_running_loop()
    first, second = FakeWS(), FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(first, loop)
    handle = remote.player("fake-sink", wav_path)
    waiting = loop.run_in_executor(None, handle.wait)
    await asyncio.sleep(0.02)
    assert not waiting.done()
    assert remote.attach(second, loop) is first
    remote.detach(first)  # the replaced connection's own close, late
    await asyncio.wait_for(waiting, 0.5)
    assert handle.finished and not handle.acknowledged
    assert second.sent == []
    # The new client is the client: the next sentence goes to it.
    later = remote.player("fake-sink", wav_path)
    await _eventually(lambda: len(second.sent) == 2)
    assert second.sent[0] == ("text", {"type": "play", "id": later.id, "format": "wav"})


# -- synthesize: the phone's own voice, asked for over the same socket -------


def _ask(loop, remote, text="Good morning.", language="english", timeout=2.0):
    """`synthesize()` blocks, as it does in the cascade's synthesis
    thread; run it off the loop so the test can play the client."""
    return loop.run_in_executor(None, lambda: remote.synthesize(text, language, timeout))


async def test_synthesize_sends_the_request_and_returns_the_tagged_wav():
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(ws, loop)
    wav = silence_wav(0.05)

    asking = _ask(loop, remote)
    await _eventually(lambda: len(ws.sent) == 1)
    kind, frame = ws.sent[0]
    assert kind == "text"
    assert frame == {
        "type": "synthesize",
        "id": frame["id"],
        "text": "Good morning.",
        "language": "english",
    }
    assert frame["id"].startswith("tts-")
    await asyncio.sleep(0.02)
    assert not asking.done()

    # Mid-hold, mic frames keep flowing around the answer: the frame
    # right after `synthesized` is the WAV, every other one is the mic.
    heard: list[bytes] = []
    remote.start_listening(heard.append)
    remote.feed(b"mic-before")
    remote.on_synthesized(frame["id"])
    remote.feed(wav)
    remote.feed(b"mic-after")
    assert await asyncio.wait_for(asking, 1.0) == wav
    assert heard == [b"mic-before", b"mic-after"]
    assert len(ws.sent) == 1  # nothing else was sent

    # Ids are fresh per request and separate from play ids.
    later = _ask(loop, remote, text="Did you sleep well?")
    await _eventually(lambda: len(ws.sent) == 2)
    second_id = ws.sent[1][1]["id"]
    assert second_id != frame["id"]
    remote.on_synthesized(second_id)
    remote.feed(wav)
    assert await asyncio.wait_for(later, 1.0) == wav


async def test_synthesize_times_out_and_a_late_answer_is_eaten_not_heard(caplog):
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(ws, loop)

    started = time.monotonic()  # before the wait begins, on the executor thread
    asking = _ask(loop, remote, timeout=0.1)
    await _eventually(lambda: len(ws.sent) == 1)
    request_id = ws.sent[0][1]["id"]
    with pytest.raises(RemoteSynthesisError, match="no WAV for " + request_id + " within 0.1s"):
        await asyncio.wait_for(asking, 1.0)
    assert 0.1 <= time.monotonic() - started < 0.8

    # The phone answers late, while she is being heard: the text frame
    # still tags the WAV that follows, which is dropped, never fed to
    # the session as microphone audio; the mic frame after it is.
    heard: list[bytes] = []
    remote.start_listening(heard.append)
    remote.on_synthesized(request_id)
    remote.feed(silence_wav(0.05))
    remote.feed(b"mic")
    assert heard == [b"mic"]


async def test_synthesize_with_no_client_raises_at_once():
    loop = asyncio.get_running_loop()
    remote = RemoteAudio(fallback=_never_local)
    started = time.monotonic()
    with pytest.raises(RemoteSynthesisError, match="no audio client"):
        remote.synthesize("Hello.", "english", 5.0)
    assert time.monotonic() - started < 0.5
    # A closed socket counts as no client.
    ws = FakeWS()
    remote.attach(ws, loop)
    ws.closed = True
    with pytest.raises(RemoteSynthesisError, match="no audio client"):
        remote.synthesize("Hello.", "english", 5.0)
    assert ws.sent == []


async def test_the_clients_error_answer_fails_the_request_and_no_wav_follows():
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(ws, loop)
    asking = _ask(loop, remote, language="bengali")
    await _eventually(lambda: len(ws.sent) == 1)
    request_id = ws.sent[0][1]["id"]
    heard: list[bytes] = []
    remote.start_listening(heard.append)
    remote.on_synthesized(request_id, error="no voice installed for bengali")
    with pytest.raises(RemoteSynthesisError, match="no voice installed for bengali"):
        await asyncio.wait_for(asking, 1.0)
    # Nothing is owed after an error: the next binary frame is the mic.
    remote.feed(b"mic")
    assert heard == [b"mic"]


async def test_detaching_or_replacing_the_client_releases_a_pending_synthesis():
    loop = asyncio.get_running_loop()
    first = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(first, loop)

    asking = _ask(loop, remote)
    await _eventually(lambda: len(first.sent) == 1)
    remote.detach(first)
    with pytest.raises(RemoteSynthesisError, match="left"):
        await asyncio.wait_for(asking, 1.0)

    # Replaced mid-request, with the text frame already in (the WAV
    # still owed): released the same way, and the old client's late
    # close is a stale detach that changes nothing.
    second, third = FakeWS(), FakeWS()
    remote.attach(second, loop)
    asking = _ask(loop, remote)
    await _eventually(lambda: len(second.sent) == 1)
    remote.on_synthesized(second.sent[0][1]["id"])
    assert remote.attach(third, loop) is second
    remote.detach(second)
    with pytest.raises(RemoteSynthesisError, match="replaced"):
        await asyncio.wait_for(asking, 1.0)
    assert remote.attached
    # The new client is the client; a stray binary frame is mic audio.
    heard: list[bytes] = []
    remote.start_listening(heard.append)
    remote.feed(b"mic")
    assert heard == [b"mic"]


async def test_a_second_synthesized_before_the_first_wav_fails_the_first():
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio(fallback=_never_local)
    remote.attach(ws, loop)
    wav = silence_wav(0.05)
    first = _ask(loop, remote, text="One.")
    second = _ask(loop, remote, text="Two.")
    await _eventually(lambda: len(ws.sent) == 2)
    ids = {sent["text"]: sent["id"] for _, sent in ws.sent}
    remote.on_synthesized(ids["One."])
    remote.on_synthesized(ids["Two."])  # the client broke the rule
    remote.feed(wav)
    with pytest.raises(RemoteSynthesisError, match="no WAV"):
        await asyncio.wait_for(first, 1.0)
    assert await asyncio.wait_for(second, 1.0) == wav


def test_the_default_synthesis_timeout_is_ten_seconds():
    # The number itself, as for the grace: a change fails one test.
    assert DEFAULT_SYNTHESIS_TIMEOUT_SECONDS == 10.0
