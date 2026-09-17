"""cascade.py's own tests — the debt flagged when it first shipped,
paid the same session. No real Groq calls, no real Piper model download,
no real PulseAudio playback: `client` and `voice_loader` are injected
fakes, and `_ensure_voice_model`/`play` are monkeypatched at the module
level cascade.py calls them from.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import saathi.voice.engine.cascade as cascade_module
from saathi.voice.engine.cascade import CascadeSession


class FakeTranscriptionsAPI:
    def __init__(self, text: str, language: str) -> None:
        self.text = text
        self.language = language
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text, language=self.language)


class FakeChatCompletionsAPI:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(
        self, heard: str = "hello", detected_language: str = "English", reply: str = "hi there"
    ) -> None:
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptionsAPI(heard, detected_language))
        self.chat = SimpleNamespace(completions=FakeChatCompletionsAPI(reply))


class FakeVoice:
    def __init__(self) -> None:
        self.synthesized: list[str] = []

    def synthesize_wav(self, text, wav_file) -> None:
        self.synthesized.append(text)
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 100)


@pytest.fixture
def no_real_io(monkeypatch):
    """cascade.py's own I/O — downloading/loading a Piper model, playing
    through PulseAudio — never touches the real thing in tests."""
    monkeypatch.setattr(
        cascade_module, "_ensure_voice_model", lambda name: Path(f"/fake/{name}.onnx")
    )
    monkeypatch.setattr(
        cascade_module, "play", lambda sink_id, path: SimpleNamespace(wait=lambda: None)
    )


def _session(client: FakeClient) -> tuple[CascadeSession, dict[str, FakeVoice]]:
    voices: dict[str, FakeVoice] = {}

    def voice_loader(path: str) -> FakeVoice:
        voice = FakeVoice()
        voices[path] = voice
        return voice

    session = CascadeSession("fake-sink", client=client, voice_loader=voice_loader)
    return session, voices


def test_end_turn_transcribes_and_replies(no_real_io):
    client = FakeClient(heard="what time is it", reply="It's teatime, dear.")
    session, _voices = _session(client)
    session.start()
    session.send_audio(b"\x00\x00" * 100)

    reply = session.end_turn()

    assert reply == "It's teatime, dear."
    assert client.audio.transcriptions.calls[0]["model"] == "whisper-large-v3-turbo"
    assert client.chat.completions.calls[0]["messages"][-1]["content"] == "what time is it"


def test_send_audio_chunks_are_joined_for_transcription(no_real_io):
    client = FakeClient()
    session, _voices = _session(client)
    session.start()
    session.send_audio(b"AAAA")
    session.send_audio(b"BBBB")
    session.end_turn()

    # We can't inspect the WAV bytes Groq received for their raw PCM
    # content directly here (it's re-encoded), but a second call must
    # start from an empty buffer, not the previous turn's leftovers.
    session.start()
    session.send_audio(b"CCCC")
    session.end_turn()
    assert len(client.audio.transcriptions.calls) == 2


def test_end_turn_resolves_a_supported_detected_language(no_real_io):
    client = FakeClient(detected_language="Chinese")
    session, _voices = _session(client)
    session.start()
    session.end_turn()
    assert session._last_language == "chinese"


def test_end_turn_falls_back_when_detected_language_is_unsupported(no_real_io):
    # The Portuguese-reply bug, reproduced: a detection outside the
    # supported set must not change what language Saathi replies in.
    client = FakeClient(detected_language="Portuguese")
    session, _voices = _session(client)
    session._last_language = "hindi"
    session.start()
    session.end_turn()
    assert session._last_language == "hindi"


def test_end_turn_tells_the_llm_which_language_to_reply_in(no_real_io):
    client = FakeClient(detected_language="Hindi")
    session, _voices = _session(client)
    session.start()
    session.end_turn()

    messages = client.chat.completions.calls[0]["messages"]
    system_messages = [m["content"].lower() for m in messages if m["role"] == "system"]
    assert any("hindi" in content for content in system_messages)


def test_say_speaks_with_the_voice_for_the_current_language(no_real_io):
    client = FakeClient(detected_language="Chinese")
    session, voices = _session(client)
    session.start()
    session.end_turn()  # resolves _last_language to "chinese"

    session.say("你好")

    assert len(voices) == 1
    fake_voice = next(iter(voices.values()))
    assert fake_voice.synthesized == ["你好"]


def test_voice_is_cached_across_turns_in_the_same_language(no_real_io):
    client = FakeClient(detected_language="English")
    session, voices = _session(client)
    session.start()
    session.end_turn()
    session.say("first")
    session.say("second")

    assert len(voices) == 1  # one voice loaded, reused, not reloaded


def test_missing_groq_api_key_raises(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        CascadeSession("fake-sink")


def test_unbuilt_interface_methods_raise_not_implemented(no_real_io):
    session, _voices = _session(FakeClient())
    with pytest.raises(NotImplementedError):
        session.on_audio(lambda *_args: None)
    with pytest.raises(NotImplementedError):
        session.on_intent(lambda *_args: None)
    with pytest.raises(NotImplementedError):
        session.interrupt()
