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

import json
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


def _fake_completion(content=None, tool_calls=None, prompt_tokens=42, completion_tokens=7):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeChatCompletionsAPI:
    """`responses`, if given, is popped one at a time per `create()` call
    (the last one repeats indefinitely once exhausted) — what the tool-
    calling tests use to script a first response with `tool_calls` set
    and a second, different one for the follow-up call `end_turn()`
    makes after running the tool. The plain `content=` constructor stays
    the simple case every non-tool-calling test already uses."""

    def __init__(
        self,
        content: str | None = None,
        prompt_tokens: int = 42,
        completion_tokens: int = 7,
        responses: list | None = None,
    ) -> None:
        self.calls: list[dict] = []
        if responses is not None:
            self._responses = list(responses)
        else:
            self._responses = [_fake_completion(content, None, prompt_tokens, completion_tokens)]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class FakeClient:
    def __init__(
        self,
        heard: str = "hello",
        detected_language: str = "English",
        reply: str = "hi there",
        chat_responses: list | None = None,
    ) -> None:
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptionsAPI(heard, detected_language))
        self.chat = SimpleNamespace(
            completions=FakeChatCompletionsAPI(reply, responses=chat_responses)
        )


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


def test_pcm_to_flac_bytes_round_trips_real_audio():
    # Item 3's latency investigation: cascade.py uploads FLAC now, not
    # WAV -- a real encode/decode round trip through soundfile, not a
    # mocked one, since a bug here would silently corrupt every real
    # turn's audio.
    import io

    import numpy as np
    import soundfile as sf

    from saathi.voice.engine.cascade import _pcm_to_flac_bytes

    original = (np.sin(np.linspace(0, 40 * np.pi, 16000)) * 10000).astype("<i2")
    flac_bytes = _pcm_to_flac_bytes(original.tobytes(), sample_rate=16000)

    assert flac_bytes[:4] == b"fLaC"  # a real FLAC file, not just any bytes
    decoded, sample_rate = sf.read(io.BytesIO(flac_bytes), dtype="int16")
    assert sample_rate == 16000
    assert len(decoded) == len(original)
    # FLAC is lossless -- the decoded samples must match exactly, not
    # approximately.
    assert (decoded == original).all()


def test_end_turn_uploads_flac_not_wav(no_real_playback):
    client = FakeClient()
    session, _backend = _session(client)
    session.start()
    session.send_audio(b"\x00\x00" * 100)
    session.end_turn()

    filename, audio_bytes = client.audio.transcriptions.calls[0]["file"]
    assert filename.endswith(".flac")
    assert audio_bytes[:4] == b"fLaC"


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
    assert timings.first_token_ms >= 0
    assert timings.first_tts_chunk_ms >= 0
    assert timings.prompt_tokens == 42
    assert timings.completion_tokens == 7
    assert timings.cost_usd == pytest.approx(42 * 0.80 / 1e6 + 7 * 4.00 / 1e6)


def test_pop_last_turn_timings_is_consumed_once(no_real_playback):
    session, _backend = _session(FakeClient())
    session.start()
    session.say(session.end_turn())

    assert session.pop_last_turn_timings() is not None
    assert session.pop_last_turn_timings() is None


def test_say_called_directly_without_end_turn_produces_no_timings(no_real_playback):
    # smoke.py's check_barge_in calls say() directly, skipping end_turn()
    # entirely (see cascade.py's module docstring) -- that must not
    # fabricate stt_ms/first_token_ms out of nothing.
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


def test_on_audio_is_still_unbuilt(no_real_playback):
    session, _backend = _session(FakeClient())
    with pytest.raises(NotImplementedError):
        session.on_audio(lambda *_args: None)


def test_on_intent_registers_without_raising(no_real_playback):
    # Item G: on_intent() is real now, not a stub -- see the dedicated
    # tool-calling tests below for what registering one actually does.
    session, _backend = _session(FakeClient())
    session.on_intent(lambda *_args: None)  # must not raise


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


def test_interrupt_during_first_sentence_playback_stops_the_third_sentence_from_ever_synthesizing(
    monkeypatch,
):
    """The corrected barge-in fix, updated for pipelining: sentence N+1
    synthesizes *while* N plays now, deliberately (see _speak()'s
    docstring) -- so an interrupt during "One."'s playback can no longer
    promise "Two." was never touched, only that synthesis never runs
    more than one sentence ahead of what's playing. "Three." -- two
    sentences ahead -- must still never be requested: its prefetch only
    starts once "Two." is pulled off the pipeline, which never happens
    because the interrupt is caught before that."""
    from saathi.audio.playback import PlaybackHandle

    slow_proc = _SlowFakeProc()
    monkeypatch.setattr(cascade_module, "play", lambda sink_id, path: PlaybackHandle(slow_proc))

    backend = FakeTTSBackend()
    session, _backend = _session(FakeClient(), backend=backend)

    say_thread = threading.Thread(target=session.say, args=("One. Two. Three.",))
    say_thread.start()
    time.sleep(0.05)  # let _speak() reach "One."'s playback (and "Two."'s prefetch)

    session.interrupt()
    say_thread.join(timeout=1.0)
    assert not say_thread.is_alive()

    # At most one sentence of pipelined-ahead synthesis, never two.
    assert "One." in backend.synthesized
    assert "Three." not in backend.synthesized


class _TimedFakeProc:
    """Like _SlowFakeProc, but completes on its own after a fixed
    duration instead of only on terminate() -- stands in for a real
    paplay process actually finishing a real clip, so a test can measure
    genuine playback duration instead of blocking indefinitely."""

    def __init__(self, duration_s: float) -> None:
        self._stopped = threading.Event()
        self._duration_s = duration_s

    def poll(self):
        return 0 if self._stopped.is_set() else None

    def wait(self, timeout=None) -> None:
        self._stopped.wait(timeout=self._duration_s if timeout is None else timeout)

    def terminate(self) -> None:
        self._stopped.set()


def test_pipelining_overlaps_synthesis_with_the_previous_sentences_playback(monkeypatch):
    # Real, timing-based proof that sentence N+1 synthesizes *during*
    # sentence N's playback, not only after it -- the actual thing item
    # 3's pipelining request asked for, not just "no interrupt
    # regression". Generous margins throughout: this asserts a clear,
    # qualitative speedup over sequential, not a tight bound that would
    # make this test flaky under normal CI scheduling jitter.
    from saathi.audio.playback import PlaybackHandle

    synth_delay_s = 0.05
    play_duration_s = 0.15
    num_sentences = 3

    def on_sentence(_sentence: str) -> None:
        time.sleep(synth_delay_s)

    monkeypatch.setattr(
        cascade_module,
        "play",
        lambda sink_id, path: PlaybackHandle(_TimedFakeProc(play_duration_s)),
    )

    backend = FakeTTSBackend(on_sentence=on_sentence)
    session, _backend = _session(FakeClient(), backend=backend)

    text = " ".join(f"Sentence {i}." for i in range(num_sentences))
    started_at = time.monotonic()
    session.say(text)
    elapsed_s = time.monotonic() - started_at

    sequential_s = num_sentences * (synth_delay_s + play_duration_s)
    # Comfortably below fully-sequential timing (proves real overlap
    # happened) but not asserting a specific tight pipelined number
    # (which would be sensitive to scheduling jitter on a loaded CI box).
    assert elapsed_s < sequential_s - synth_delay_s
    assert backend.synthesized == [f"Sentence {i}." for i in range(num_sentences)]


def test_end_turn_applies_a_stored_language_preference_when_detection_is_unsupported(
    no_real_playback,
):
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


def test_a_supported_stored_preference_pins_the_language_over_organic_detection(
    no_real_playback,
):
    # The real bug this session found and fixed by hand, not a
    # hypothetical: "speak to me in Mandarin" (item G) writes a
    # preference and is documented as "effective next turn" -- every
    # next turn, not just the ones where she happens not to speak
    # English again. A supported detection that used to win outright
    # made the switch invisible the instant she spoke a sentence Whisper
    # detected as English, which is the overwhelmingly common case,
    # since asking for the switch itself usually happens in whatever
    # language she was already speaking. Verified against a real Groq
    # call before this fix landed: asking (in English) to switch to
    # Mandarin correctly wrote the preference, but the very next turn's
    # English audio silently reverted it.
    client = FakeClient(detected_language="English")  # a supported, *different* detection
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


def test_a_pinned_language_preference_persists_across_multiple_turns(no_real_playback):
    client = FakeClient(detected_language="English")
    session = CascadeSession(
        "fake-sink",
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
        language_preference=lambda: "chinese",
    )
    session.start()
    session.end_turn()
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


def test_construction_preloads_the_backends_voice_immediately():
    # Kills the cold-start cost on the *first* reply, not just the
    # second one onward: without this, only end_turn() (below) warmed
    # anything, so a fresh process's first turn always paid the full
    # cold-load cost on top of its own synthesis.
    preload_calls: list[str] = []

    class PreloadingFakeBackend(FakeTTSBackend):
        def preload(self, language: str) -> None:
            preload_calls.append(language)

    CascadeSession(
        "fake-sink",
        client=FakeClient(),
        backends={"fake": PreloadingFakeBackend()},
        backend_preference=lambda: "fake",
    )
    assert preload_calls == ["english"]  # DEFAULT_LANGUAGE


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
    preload_calls.clear()  # construction itself already preloaded once -- see the test above
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


# -- item G: tool calling (on_intent, set_language's real entry point) ----


def _fake_tool_call(call_id, name, arguments: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def test_a_tool_call_emits_an_intent_and_speaks_the_follow_up_reply(no_real_playback):
    tool_call = _fake_tool_call("call_1", "set_language", {"language": "chinese"})
    client = FakeClient(
        chat_responses=[
            _fake_completion(content=None, tool_calls=[tool_call]),
            _fake_completion(content="好的,我现在会说中文了。"),
        ]
    )
    session, _backend = _session(client)
    received_intents = []
    session.on_intent(lambda name, args: received_intents.append((name, args)) or {"status": "ok"})

    session.start()
    reply = session.end_turn()

    assert received_intents == [("set_language", {"language": "chinese"})]
    assert reply == "好的,我现在会说中文了。"
    # Two real chat completion calls were made: the one that asked for
    # the tool, and the follow-up that turned its result into words.
    assert len(client.chat.completions.calls) == 2


def test_a_tool_calls_result_is_fed_back_as_a_tool_message(no_real_playback):
    tool_call = _fake_tool_call("call_1", "set_language", {"language": "hindi"})
    client = FakeClient(
        chat_responses=[
            _fake_completion(content=None, tool_calls=[tool_call]),
            _fake_completion(content="ठीक है।"),
        ]
    )
    session, _backend = _session(client)
    session.on_intent(lambda name, args: {"status": "ok", "language": args["language"]})

    session.start()
    session.end_turn()

    follow_up_messages = client.chat.completions.calls[1]["messages"]
    tool_messages = [m for m in follow_up_messages if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert json.loads(tool_messages[0]["content"]) == {"status": "ok", "language": "hindi"}


def test_a_tool_call_with_no_registered_intent_handler_still_gets_a_reply(no_real_playback):
    # Nothing has called on_intent() yet -- must not crash the turn.
    tool_call = _fake_tool_call("call_1", "set_language", {"language": "chinese"})
    client = FakeClient(
        chat_responses=[
            _fake_completion(content=None, tool_calls=[tool_call]),
            _fake_completion(content="Sorry, I can't do that right now."),
        ]
    )
    session, _backend = _session(client)
    session.start()
    reply = session.end_turn()
    assert reply == "Sorry, I can't do that right now."


def test_tool_call_token_usage_sums_both_completion_calls(no_real_playback):
    tool_call = _fake_tool_call("call_1", "set_language", {"language": "english"})
    client = FakeClient(
        chat_responses=[
            _fake_completion(
                content=None, tool_calls=[tool_call], prompt_tokens=100, completion_tokens=20
            ),
            _fake_completion(content="Sure.", prompt_tokens=150, completion_tokens=5),
        ]
    )
    session, _backend = _session(client)
    session.on_intent(lambda name, args: {"status": "ok"})
    session.start()
    session.say(session.end_turn())

    timings = session.pop_last_turn_timings()
    assert timings.prompt_tokens == 250  # 100 + 150
    assert timings.completion_tokens == 25  # 20 + 5


def test_no_tool_schemas_means_no_tools_param_is_sent(no_real_playback):
    client = FakeClient()
    session, _backend = _session(client)  # no tool_schemas passed to CascadeSession
    session.start()
    session.end_turn()
    assert client.chat.completions.calls[0]["tools"] is None


def test_tool_schemas_are_passed_through_to_the_chat_completion_call(no_real_playback):
    schema = [{"type": "function", "function": {"name": "set_language", "parameters": {}}}]
    session = CascadeSession(
        "fake-sink",
        client=FakeClient(),
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
        tool_schemas=schema,
    )
    session.start()
    session.end_turn()
    calls = session._client.chat.completions.calls
    assert calls[0]["tools"] == schema
