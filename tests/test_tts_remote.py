"""voice/tts/remote_backend.py -- the phone's own voice behind the
`TTSBackend` interface. Against a fake `RemoteAudio` for the contract
(available iff attached; one WAV per sentence, lazily; a failed sentence
is a short silence, never a raise), and against the real seam with a
fake socket for the whole path: a `CascadeSession.say()` whose sentence
is rendered by the "phone" and then played back through it, with the
engine none the wiser.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from types import SimpleNamespace

import pytest

from saathi.audio.remote import (
    DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
    RemoteAudio,
    RemoteSynthesisError,
)
from saathi.voice.engine.cascade import CascadeSession
from saathi.voice.tts import TTSBackend
from saathi.voice.tts.remote_backend import REMOTE_BACKEND_ID, RemoteTTSBackend, silent_wav


class FakeRemote:
    """`RemoteAudio` as the backend sees it: `attached`, `synthesize()`."""

    def __init__(self, wav: bytes = b"WAV", error: str | None = None) -> None:
        self.attached = False
        self.wav = wav
        self.error = error
        self.calls: list[tuple[str, str, float]] = []

    def synthesize(self, text: str, language: str, timeout: float) -> bytes:
        self.calls.append((text, language, timeout))
        if self.error is not None:
            raise RemoteSynthesisError(self.error)
        return self.wav


def _wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def test_it_is_a_local_free_backend_with_the_agreed_id_and_name():
    backend = RemoteTTSBackend(FakeRemote())
    assert isinstance(backend, TTSBackend)
    assert backend.id == REMOTE_BACKEND_ID == "android-tts"
    assert backend.display_name == "Phone voice"
    assert backend.local is True
    assert backend.cost_per_million_chars_usd() == 0.0


def test_available_iff_a_client_is_attached():
    remote = FakeRemote()
    backend = RemoteTTSBackend(remote)
    assert backend.available() == (False, "no phone is attached on /audio")
    remote.attached = True
    assert backend.available() == (True, "")
    remote.attached = False
    assert backend.available()[0] is False


def test_synthesize_stream_asks_for_one_sentence_at_a_time_with_the_timeout():
    remote = FakeRemote(wav=b"rendered")
    backend = RemoteTTSBackend(remote, timeout_seconds=4.5)
    stream = backend.synthesize_stream("hindi", ["One.", "Two."])
    assert remote.calls == []  # lazy: nothing asked until pulled
    assert next(stream) == b"rendered"
    assert remote.calls == [("One.", "hindi", 4.5)]
    assert next(stream) == b"rendered"
    assert remote.calls[-1] == ("Two.", "hindi", 4.5)
    assert list(stream) == []
    assert RemoteTTSBackend(remote)._timeout_seconds == DEFAULT_SYNTHESIS_TIMEOUT_SECONDS


def test_a_failed_sentence_is_a_short_silence_with_a_warning_never_a_raise(caplog):
    remote = FakeRemote(error="no audio client is attached")
    backend = RemoteTTSBackend(remote)
    with caplog.at_level(logging.WARNING):
        chunks = list(backend.synthesize_stream("english", ["Hello.", "Still here."]))
    assert len(chunks) == 2
    for chunk in chunks:
        assert _wav_seconds(chunk) == pytest.approx(0.1)
    assert chunks[0] == silent_wav()
    assert "no audio client is attached" in caplog.text
    assert "'Hello.'" in caplog.text and "'Still here.'" in caplog.text
    # Every sentence was still asked for: one failure does not end the reply.
    assert [call[0] for call in remote.calls] == ["Hello.", "Still here."]


# -- the whole path: cascade -> backend -> seam -> "phone" -> seam -> cascade --


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        import json

        self.sent.append(("text", json.loads(data)))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(("binary", data))


def _silence(seconds: float) -> bytes:
    return silent_wav(seconds, 22050)


async def _eventually(condition, seconds: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.005)


def _fake_client() -> SimpleNamespace:
    return SimpleNamespace(
        audio=SimpleNamespace(transcriptions=None), chat=SimpleNamespace(completions=None)
    )


async def test_a_cascade_reply_is_rendered_by_the_phone_and_played_back_through_it():
    loop = asyncio.get_running_loop()
    ws = FakeWS()
    remote = RemoteAudio()
    remote.attach(ws, loop)
    phone = RemoteTTSBackend(remote, timeout_seconds=2.0)
    session = CascadeSession(
        "remote",
        client=_fake_client(),
        backends={phone.id: phone},
        backend_preference=lambda: "google-chirp3-hd",  # not in the dict: falls through
        speech_gate=lambda pcm: True,
        player=remote.player,
    )
    assert session._current_backend() is phone

    rendered = {"Good morning.": _silence(0.05), "Did you sleep well?": _silence(0.08)}
    saying = loop.run_in_executor(None, session.say, "Good morning. Did you sleep well?")
    # The "phone", on the loop thread as the server would be: answer
    # each synthesize request with its WAV, ack each play once its WAV
    # has landed. The order in which the next sentence's request and
    # the previous sentence's play go out is the pipeline's business.
    answered: set[str] = set()
    acked: set[str] = set()
    plays: list[bytes] = []
    deadline = loop.time() + 3.0
    while not saying.done():
        assert loop.time() < deadline, "say() never finished"
        for index, (kind, frame) in enumerate(list(ws.sent)):
            if kind != "text":
                continue
            if frame["type"] == "synthesize" and frame["id"] not in answered:
                answered.add(frame["id"])
                remote.on_synthesized(frame["id"])
                remote.feed(rendered[frame["text"]])
            elif frame["type"] == "play" and frame["id"] not in acked:
                if index + 1 >= len(ws.sent):
                    break  # its WAV hasn't landed yet
                body = ws.sent[index + 1]
                assert body[0] == "binary"
                acked.add(frame["id"])
                plays.append(body[1])
                remote.on_played(frame["id"])
        await asyncio.sleep(0.005)
    await asyncio.wait_for(saying, 0.5)

    asked = [
        frame["text"]
        for kind, frame in ws.sent
        if kind == "text" and frame["type"] == "synthesize"
    ]
    assert asked == ["Good morning.", "Did you sleep well?"]
    assert plays == [rendered["Good morning."], rendered["Did you sleep well?"]]
    assert len(ws.sent) == 6  # ask, play header, WAV -- twice; nothing else
