"""The engine's half of 2026-10-08's requests: her name is the trigger,
never the content ("Saathi", the old name, alone was transcribed "Saudi?" and
answered), and while music plays the transcriber and the router lean
toward media commands. No real Groq/OpenAI, TTS or audio: the same
fakes as test_cascade.py."""

from types import SimpleNamespace

import pytest

import saathi.voice.engine.cascade as cascade_module
from saathi.voice.engine.cascade import MEDIA_STT_PROMPT, NAME_PROMPTS, CascadeSession
from tests.test_cascade import FakeClient, FakeTTSBackend, _hears_speech


@pytest.fixture(autouse=True)
def no_real_playback(monkeypatch):
    monkeypatch.setattr(
        cascade_module, "play", lambda sink_id, path: SimpleNamespace(wait=lambda: None)
    )


def _session(client, media_playing=None, intents=None):
    session = CascadeSession(
        "fake-sink",
        speech_gate=_hears_speech,
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
        media_playing=media_playing,
    )
    if intents is not None:
        session.on_intent(lambda name, args: intents.append((name, args)) or {"status": "ok"})
    return session


def _turn(session, by_name=False) -> str:
    session.start()
    if by_name:
        session.started_by_name()
    session.send_audio(b"\x00\x00" * 100)
    return session.end_turn()


def test_her_name_alone_is_no_turn_and_no_model_call():
    client = FakeClient(heard="Kaki?")
    session = _session(client)
    assert _turn(session, by_name=True) == ""
    assert session.heard_only_name is True
    assert client.chat.completions.calls == []
    assert session.last_heard is None  # nothing to caption


def test_cocky_alone_on_a_wake_turn_is_her_name_too():
    client = FakeClient(heard="Cocky?")
    session = _session(client)
    assert _turn(session, by_name=True) == ""
    assert session.heard_only_name is True
    assert client.chat.completions.calls == []


def test_cocky_over_the_spacebar_is_not_her_name():
    # Only a turn that began with her name reads the cloud's odder
    # spellings as the name: "Cocky?" pressed-and-said is her words.
    client = FakeClient(heard="Cocky?", reply="I said it's teatime.")
    session = _session(client)
    assert _turn(session) == "I said it's teatime."
    assert session.heard_only_name is False


def test_name_and_request_reach_the_model_as_the_request_alone():
    client = FakeClient(heard="Kaki, what's the weather like?", reply="Sunny.")
    session = _session(client)
    assert _turn(session, by_name=True) == "Sunny."
    assert client.chat.completions.calls[0]["messages"][-1]["content"] == "what's the weather like?"
    assert session.last_heard == "what's the weather like?"


def test_kaki_play_shape_of_you_reaches_the_model_as_play_shape_of_you():
    client = FakeClient(heard="Kaki play Shape of You", reply="")
    session = _session(client)
    _turn(session, by_name=True)
    assert client.chat.completions.calls[0]["messages"][-1]["content"] == "play Shape of You"


def test_a_follow_up_that_starts_with_her_name_is_just_stripped():
    client = FakeClient(heard="Khaki, and tomorrow?", reply="Rain.")
    session = _session(client)
    assert _turn(session) == "Rain."
    assert client.chat.completions.calls[0]["messages"][-1]["content"] == "and tomorrow?"


def test_the_name_prompt_varies_and_speaks_her_language():
    session = _session(FakeClient())
    lines = [session.name_prompt() for _ in range(len(NAME_PROMPTS["english"]))]
    assert len(set(lines)) == len(lines)
    assert all(not line.lower().startswith(("sure", "certainly")) for line in lines)
    session._last_language = "hindi"
    assert session.name_prompt() in NAME_PROMPTS["hindi"]


def test_while_music_plays_the_transcriber_is_primed_with_media_commands():
    playing = {"value": True}
    client = FakeClient(heard="what's this song", reply="Shape of You.")
    session = _session(client, media_playing=lambda: playing["value"])
    _turn(session)
    assert client.audio.transcriptions.calls[0]["prompt"] == MEDIA_STT_PROMPT
    playing["value"] = False
    _turn(session)
    assert "prompt" not in client.audio.transcriptions.calls[1]


def test_kaki_stop_mid_song_is_routed_without_the_model():
    intents: list = []
    client = FakeClient(heard="in love with your body, Kaki, stop.")
    session = _session(client, media_playing=lambda: True, intents=intents)
    assert _turn(session, by_name=True) == "Stopped."
    assert intents == [("play_music", {"action": "stop"})]
    assert client.chat.completions.calls == []


def test_her_name_then_silence_is_still_her_calling():
    # The wake listener heard the name as a whole utterance and handed
    # over only what came after: nothing. Still a call, not nothing.
    client = FakeClient(heard="")
    session = CascadeSession(
        "fake-sink",
        speech_gate=lambda pcm: False,
        client=client,
        backends={"fake": FakeTTSBackend()},
        backend_preference=lambda: "fake",
    )
    assert _turn(session, by_name=True) == ""
    assert session.heard_only_name is True
    assert _turn(session) == ""
    assert session.heard_only_name is False  # over the spacebar: nothing said
