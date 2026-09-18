"""cascade.py's own tests. No real Groq calls, no real TTS backend, no
real PulseAudio playback: `client` and the TTS backend are injected
fakes, and `play` is monkeypatched at the module level `_speak()` calls
it from.

`_speak()` used to synthesize a whole reply in one blocking call and
these tests used to exercise that directly (a `FakeVoice` with
`synthesize_wav`). It now goes through `TTSBackend.synthesize_stream()`,
sentence by sentence, specifically so a press can interrupt *between*
sentences on Pi-class hardware where one sentence's synthesis can itself
take a while — see cascade.py's docstring for why that distinction
matters and isn't debt. `FakeTTSBackend` below stands in for whatever
real backend is selected.
"""

import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

import saathi.voice.engine.cascade as cascade_module
from saathi.identity.store import IdentityStore
from saathi.voice.engine.cascade import CascadeSession
from saathi.voice.tts import TTSBackend


class FakeTranscriptionsAPI:
    def __init__(self, text: str, language: str) -> None:
        self.text = text
        self.language = language
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text, language=self.language)


class FakeChatCompletionsAPI:
    def __init__(self, content: str, prompt_tokens: int = 42, completion_tokens: int = 7) -> None:
        self.content = content
        self.calls: list[dict] = []
        self._usage = SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=self._usage)


class FakeClient:
    def __init__(
        self, heard: str = "hello", detected_language: str = "English", reply: str = "hi there"
    ) -> None:
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptionsAPI(heard, detected_language))
        self.chat = SimpleNamespace(completions=FakeChatCompletionsAPI(reply))


class FakeTTSBackend(TTSBackend):
    """A `TTSBackend` that never touches real audio: each sentence
    becomes a one-element bytes object recording that it was asked for.
    `on_sentence`, if given, runs synchronously before yielding each
    sentence's "audio" — tests use it to synchronize with a synthesis
    call in progress, the same way a slow real backend would occupy that
    window."""

    id = "fake"
    display_name = "Fake"
    license = "n/a"
    local = True

    def __init__(self, on_sentence=None) -> None:
        self.synthesized: list[str] = []
        self.languages_asked: list[str] = []
        self._on_sentence = on_sentence

    def available(self) -> tuple[bool, str]:
        return True, ""

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        self.languages_asked.append(language)
        for sentence in sentences:
            if self._on_sentence is not None:
                self._on_sentence(sentence)
            self.synthesized.append(sentence)
            yield b"\x00\x00" * 10

    def cost_per_million_chars_usd(self) -> float:
        return 0.0


class UnavailableFakeBackend(TTSBackend):
    id = "unavailable"
    display_name = "Unavailable"
    license = "n/a"
    local = False

    def available(self) -> tuple[bool, str]:
        return False, "no credentials"

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        raise AssertionError("must never be called: available() is False")

    def cost_per_million_chars_usd(self) -> float:
        return 1.0


@pytest.fixture
def no_real_playback(monkeypatch):
    monkeypatch.setattr(
        cascade_module, "play", lambda sink_id, path: SimpleNamespace(wait=lambda: None)
    )


def _session(client: FakeClient, backend: FakeTTSBackend | None = None):
    backend = backend or FakeTTSBackend()
    session = CascadeSession(
        "fake-sink",
        client=client,
        backends={"fake": backend},
        backend_preference=lambda: "fake",
    )
    return session, backend


def test_end_turn_transcribes_and_replies(no_real_playback):
    client = FakeClient(heard="what time is it", reply="It's teatime, dear.")
    session, _backend = _session(client)
    session.start()
    session.send_audio(b"\x00\x00" * 100)

    reply = session.end_turn()

    assert reply == "It's teatime, dear."
    assert client.audio.transcriptions.calls[0]["model"] == "whisper-large-v3-turbo"
    assert client.chat.completions.calls[0]["messages"][-1]["content"] == "what time is it"


def test_send_audio_chunks_are_joined_for_transcription(no_real_playback):
    client = FakeClient()
    session, _backend = _session(client)
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


def test_end_turn_resolves_a_supported_detected_language(no_real_playback):
    client = FakeClient(detected_language="Chinese")
    session, _backend = _session(client)
    session.start()
    session.end_turn()
    assert session._last_language == "chinese"


def test_end_turn_falls_back_when_detected_language_is_unsupported(no_real_playback):
    # The Portuguese-reply bug, reproduced: a detection outside the
    # supported set must not change what language Saathi replies in.
    client = FakeClient(detected_language="Portuguese")
    session, _backend = _session(client)
    session._last_language = "hindi"
    session.start()
    session.end_turn()
    assert session._last_language == "hindi"


def test_end_turn_tells_the_llm_which_language_to_reply_in(no_real_playback):
    client = FakeClient(detected_language="Hindi")
    session, _backend = _session(client)
    session.start()
    session.end_turn()

    messages = client.chat.completions.calls[0]["messages"]
    system_messages = [m["content"].lower() for m in messages if m["role"] == "system"]
    assert any("hindi" in content for content in system_messages)


def test_say_synthesizes_one_sentence_at_a_time_via_the_selected_backend(no_real_playback):
    client = FakeClient(detected_language="Chinese")
    session, backend = _session(client)
    session.start()
    session.end_turn()  # resolves _last_language to "chinese"

    session.say("First sentence. Second sentence.")

    assert backend.synthesized == ["First sentence.", "Second sentence."]
    assert backend.languages_asked[-1] == "chinese"


def test_a_full_turn_produces_timings_with_tokens(no_real_playback):
    client = FakeClient(reply="First sentence. Second sentence.")
    session, _backend = _session(client)
    session.start()
    reply = session.end_turn()
    session.say(reply)

    timings = session.pop_last_turn_timings()
    assert timings is not None
    assert timings.stt_ms >= 0
    assert timings.llm_ms >= 0
    assert timings.first_tts_chunk_ms >= 0
    assert timings.prompt_tokens == 42
    assert timings.completion_tokens == 7


def test_pop_last_turn_timings_is_consumed_once(no_real_playback):
    session, _backend = _session(FakeClient())
    session.start()
    session.say(session.end_turn())

    assert session.pop_last_turn_timings() is not None
    assert session.pop_last_turn_timings() is None


def test_say_called_directly_without_end_turn_produces_no_timings(no_real_playback):
    # smoke.py's check_barge_in calls say() directly, skipping end_turn()
    # entirely (see cascade.py's module docstring) -- that must not
    # fabricate stt_ms/llm_ms out of nothing.
    session, _backend = _session(FakeClient())
    session.say("Hello there.")
    assert session.pop_last_turn_timings() is None


def test_say_with_empty_text_synthesizes_nothing(no_real_playback):
    session, backend = _session(FakeClient())
    session.say("")
    assert backend.synthesized == []


def test_missing_groq_api_key_raises(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        CascadeSession("fake-sink")


def test_unbuilt_interface_methods_raise_not_implemented(no_real_playback):
    session, _backend = _session(FakeClient())
    with pytest.raises(NotImplementedError):
        session.on_audio(lambda *_args: None)
    with pytest.raises(NotImplementedError):
        session.on_intent(lambda *_args: None)


def test_interrupt_with_nothing_playing_is_a_quiet_no_op(no_real_playback):
    session, _backend = _session(FakeClient())
    session.interrupt()  # must not raise


def test_current_backend_falls_back_to_piper_when_preferred_is_unavailable(no_real_playback):
    piper_stand_in = FakeTTSBackend()
    piper_stand_in.id = "piper"
    session = CascadeSession(
        "fake-sink",
        client=FakeClient(),
        backends={"piper": piper_stand_in, "unavailable": UnavailableFakeBackend()},
        backend_preference=lambda: "unavailable",
    )
    assert session._current_backend() is piper_stand_in


def test_current_backend_falls_back_to_piper_for_an_unknown_preference(no_real_playback):
    piper_stand_in = FakeTTSBackend()
    piper_stand_in.id = "piper"
    session = CascadeSession(
        "fake-sink",
        client=FakeClient(),
        backends={"piper": piper_stand_in},
        backend_preference=lambda: "some-backend-that-was-removed",
    )
    assert session._current_backend() is piper_stand_in


class _SlowFakeProc:
    """Stands in for the real `subprocess.Popen` `play()` wraps: only
    returns from `.wait()` / unblocks once `.terminate()` is called,
    exactly like a real paplay process reacting to SIGTERM."""

    def __init__(self) -> None:
        self._stopped = threading.Event()

    def poll(self):
        return 0 if self._stopped.is_set() else None

    def wait(self, timeout=None) -> None:
        self._stopped.wait(timeout=timeout)

    def terminate(self) -> None:
        self._stopped.set()


def test_interrupt_stops_a_blocking_say_call(monkeypatch):
    from saathi.audio.playback import PlaybackHandle

    slow_proc = _SlowFakeProc()
    monkeypatch.setattr(cascade_module, "play", lambda sink_id, path: PlaybackHandle(slow_proc))

    session, _backend = _session(FakeClient(), backend=FakeTTSBackend())

    say_thread = threading.Thread(target=session.say, args=("a long reply",))
    say_thread.start()
    time.sleep(0.05)  # let _speak() reach handle.wait()
    assert say_thread.is_alive()

    session.interrupt()
    say_thread.join(timeout=1.0)
    assert not say_thread.is_alive()


def test_interrupt_during_first_sentence_playback_stops_the_second_sentence_from_ever_synthesizing(
    monkeypatch,
):
    """The corrected barge-in fix, under direct test: an interrupt that
    lands while one sentence is still playing must stop *before* the
    next sentence is synthesized, not just before it's played. This is
    what bounds the un-interruptible window to one sentence instead of
    the whole reply."""
    from saathi.audio.playback import PlaybackHandle

    slow_proc = _SlowFakeProc()
    monkeypatch.setattr(cascade_module, "play", lambda sink_id, path: PlaybackHandle(slow_proc))

    backend = FakeTTSBackend()
    session, _backend = _session(FakeClient(), backend=backend)

    say_thread = threading.Thread(target=session.say, args=("One. Two. Three.",))
    say_thread.start()
    time.sleep(0.05)  # let _speak() synthesize + start playing "One."
    assert backend.synthesized == ["One."]

    session.interrupt()
    say_thread.join(timeout=1.0)
    assert not say_thread.is_alive()

    # "Two." and "Three." must never have been requested from the
    # backend -- that's the whole point of checking the interrupt flag
    # before each next(stream) call, not just before each play().
    assert backend.synthesized == ["One."]


def test_end_turn_applies_a_stored_language_preference_when_detection_is_unsupported(
    no_real_playback,
):
    # A supported live detection still wins over a stored preference for
    # that turn's reply (resolve_language()'s existing, deliberate
    # behavior -- see voice/language.py and the Portuguese-reply bug it
    # was built to fix: Saathi mirrors whatever language she's actually
    # speaking). Where a stored preference shows through is exactly
    # where an unsupported/failed detection would otherwise have fallen
    # back to whatever self._last_language happened to already be --
    # the preference makes that fallback a deliberate choice (set via
    # the panel or, eventually, a spoken command) instead of an accident
    # of session history.
    client = FakeClient(detected_language="Portuguese")  # unsupported either way
    session = CascadeSession(
        "fake-sink",
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
        language_preference=lambda: "chinese",
    )
    session.start()
    session.end_turn()
    assert session._last_language == "chinese"


def test_end_turn_ignores_an_unsupported_stored_language_preference(no_real_playback):
    client = FakeClient(detected_language="Portuguese")  # unsupported either way
    session = CascadeSession(
        "fake-sink",
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
        language_preference=lambda: "klingon",
    )
    session._last_language = "hindi"
    session.start()
    session.end_turn()
    assert session._last_language == "hindi"


def test_end_turn_preloads_the_current_backends_voice(no_real_playback):
    # A backend without a model to warm (e.g. Google) simply has no
    # preload() attribute -- see cascade.py's _preload_voice_in_background
    # docstring -- so this only exercises backends that define one.
    preload_calls: list[str] = []

    class PreloadingFakeBackend(FakeTTSBackend):
        def preload(self, language: str) -> None:
            preload_calls.append(language)

    client = FakeClient(detected_language="English")
    session, _backend = _session(client, backend=PreloadingFakeBackend())
    session.start()
    session.end_turn()

    assert preload_calls == ["english"]


def test_interrupt_requested_flag_is_reset_at_the_start_of_each_say_call(no_real_playback):
    session, backend = _session(FakeClient())
    session.interrupt()  # nothing playing yet, but sets the flag
    session.say("Hello there.")
    # A stale flag from a previous (or no-op) interrupt must not silently
    # swallow the next turn's speech.
    assert backend.synthesized == ["Hello there."]


# -- item F: compiled context from IdentityStore --------------------------


def _tmp_store() -> IdentityStore:
    tmp_dir = tempfile.mkdtemp()
    store = IdentityStore(Path(tmp_dir) / "identity.sqlite3")
    store.create()
    return store


def test_no_identity_store_keeps_the_old_static_persona_behavior(no_real_playback):
    # Every caller that doesn't know about IdentityStore yet -- including
    # every other test in this file -- must see exactly the old behavior.
    client = FakeClient()
    session = CascadeSession(
        "fake-sink",
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
    )
    session.start()
    session.end_turn()
    system_messages = [m["content"] for m in client.chat.completions.calls[0]["messages"]]
    assert system_messages[0] == cascade_module._PERSONA_PATH.read_text().strip()


def test_an_identity_store_compiles_context_at_construction(no_real_playback):
    store = _tmp_store()
    store.append(
        "rules",
        text="She likes being greeted by name.",
        confidence=0.9,
        learned_at="2026-09-18T00:00:00+00:00",
        active=1,
    )
    client = FakeClient()
    session = CascadeSession(
        "fake-sink",
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
        identity_store=store,
    )
    session.start()
    session.end_turn()
    system_messages = [m["content"] for m in client.chat.completions.calls[0]["messages"]]
    assert "She likes being greeted by name." in system_messages[0]
    store.close()


def test_context_is_refreshed_in_the_background_after_say_completes(no_real_playback):
    store = _tmp_store()
    client = FakeClient()
    session = CascadeSession(
        "fake-sink",
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
        identity_store=store,
    )

    # A rule written *after* construction must not appear yet -- nothing
    # has recompiled the context since __init__.
    store.append(
        "rules",
        text="She prefers tea over coffee.",
        confidence=0.9,
        learned_at="2026-09-18T00:00:00+00:00",
        active=1,
    )
    assert "tea" not in session._compiled_context

    session.start()
    session.say("a reply")  # the turn ending is what triggers the refresh

    deadline = time.monotonic() + 1.0
    while "tea" not in session._compiled_context and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "tea" in session._compiled_context
    store.close()
