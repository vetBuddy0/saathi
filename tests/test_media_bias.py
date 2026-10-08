"""2026-10-08: while music plays, media commands are recognised fast and
by rule (voice/router.py), her name is stripped and recognised in all
its spellings (audio/wake.py), and the default sink goes through the
echo canceller (audio/aec.py)."""

import pytest

from saathi.audio.aec import EchoCancelHandles, SystemEchoCancel
from saathi.audio.wake import is_only_name, strip_wake_word
from saathi.core import Core, Event, State
from saathi.voice.router import Command, Context, route, sounds_complete

PLAYING = Context(media_playing=True)


@pytest.mark.parametrize(
    "heard, action",
    [
        ("Kaki, stop.", "stop"),
        ("Pause.", "pause"),
        ("Resume", "resume"),
        ("Volume up please", "louder"),
        ("volume down", "quieter"),
        ("Louder!", "louder"),
        ("Softer.", "quieter"),
        ("Next.", "next"),
        ("Next song", "next"),
        ("Reduce the volume", "quieter"),
        ("baby I'm in love with your body Kaki stop", "stop"),
        ("...shape of you. Khaki, pause.", "pause"),
    ],
)
def test_media_commands_while_playing_are_routed(heard, action):
    command = route(heard, context=PLAYING)
    assert command is not None and command.tool == "play_music"
    assert command.arguments == {"action": action}


@pytest.mark.parametrize(
    "heard",
    [
        "What's this song called?",
        "I don't want you to stop",
        "Kaki, what's the weather tomorrow?",
    ],
)
def test_anything_else_while_playing_still_goes_to_the_model(heard):
    assert route(heard, context=PLAYING) is None


def test_lyrics_ending_in_stop_without_her_name_are_not_taken_mid_sentence():
    assert route("never gonna stop believing in you", context=PLAYING) is None


def test_without_music_stop_still_belongs_to_the_model():
    assert route("stop") is None


def test_kaki_stop_ends_at_the_short_pause_while_music_plays():
    assert sounds_complete("Kaki stop", media_playing=True)
    assert sounds_complete("Kaki, volume up", media_playing=True)
    assert not sounds_complete("Kaki stop", media_playing=False)


def test_a_routed_stop_says_stopped():
    assert route("Kaki stop", context=PLAYING) == Command(
        "play_music", {"action": "stop"}, "Stopped."
    )


@pytest.mark.parametrize(
    "text, loose, only",
    [
        ("Kaki?", False, True),
        ("Kaki.", True, True),
        ("Hey Kaki", False, True),
        ("Cocky?", True, True),
        ("Cocky?", False, False),
        ("Kacky?", True, True),
        ("Kacky?", False, False),
        ("Kaki Kaki", False, True),
        ("Kaki, play a song", True, False),
        ("", True, False),
        ("Cocky guy", True, False),
    ],
)
def test_is_only_name(text, loose, only):
    assert is_only_name(text, loose=loose) is only


def test_strip_takes_the_loose_spellings_only_when_asked():
    assert strip_wake_word("Cocky, play Shape of You", loose=True) == "play Shape of You"
    assert strip_wake_word("Cocky people annoy me") == "Cocky people annoy me"
    assert strip_wake_word("Kaki... Kaki, play a song") == "play a song"


# -- the default sink through the canceller ----------------------------------


class FakePactl:
    def __init__(self, default: str, sinks: list[str]) -> None:
        self.default = default
        self.sinks = sinks
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args == ["get-default-sink"]:
            return self.default + "\n"
        if args == ["list", "short", "sinks"]:
            return "".join(f"{i}\t{name}\tPipeWire\n" for i, name in enumerate(self.sinks))
        if args[0] == "set-default-sink":
            self.default = args[1]
        return ""


HANDLES = EchoCancelHandles(module_index="7", source_id="ec-source", sink_id="ec-sink")


def test_the_default_sink_goes_through_the_canceller_and_comes_back():
    pactl = FakePactl("speaker", ["speaker", "ec-sink"])
    restore = SystemEchoCancel(pactl).route_default_through(HANDLES, "speaker")
    assert pactl.default == "ec-sink"
    restore()
    assert pactl.default == "speaker"
    restore()  # once only
    assert [c for c in pactl.calls if c[0] == "set-default-sink"] == [
        ["set-default-sink", "ec-sink"],
        ["set-default-sink", "speaker"],
    ]


def test_restore_leaves_a_default_someone_else_chose_since():
    pactl = FakePactl("speaker", ["speaker", "ec-sink", "headphones"])
    restore = SystemEchoCancel(pactl).route_default_through(HANDLES, "speaker")
    pactl.default = "headphones"
    restore()
    assert pactl.default == "headphones"


def test_after_a_crash_left_it_routed_restore_goes_to_the_detected_speaker():
    pactl = FakePactl("ec-sink", ["speaker", "ec-sink"])
    restore = SystemEchoCancel(pactl).route_default_through(HANDLES, "speaker")
    restore()
    assert pactl.default == "speaker"


def test_a_sink_the_server_does_not_have_changes_nothing():
    pactl = FakePactl("speaker", ["speaker"])
    assert SystemEchoCancel(pactl).route_default_through(HANDLES, "speaker") is None
    assert not any(c[0] == "set-default-sink" for c in pactl.calls)


# -- core's new edges -----------------------------------------------------------


def test_the_open_conversation_edges():
    core = Core(initial=State.SPEAKING)
    assert core.handle(Event("follow_up")) and core.state == State.ATTENTIVE
    assert core.handle(Event("confirm")) and core.state == State.LISTENING
    core = Core(initial=State.ATTENTIVE)
    assert core.handle(Event("dismiss")) and core.state == State.IDLE
    core = Core(initial=State.ATTENTIVE)
    assert core.handle(Event("press")) and core.state == State.LISTENING
    core = Core(initial=State.THINKING)
    assert core.handle(Event("name_only")) and core.state == State.ATTENTIVE
    assert core.handle(Event("prompt")) and core.state == State.SPEAKING


def test_the_wake_transcriber_is_primed_with_media_commands_only_while_playing(monkeypatch):
    import sys
    import types

    from saathi.audio.wake import local_transcriber

    prompts = []

    class FakeWhisper:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def transcribe(self, audio, **kwargs):
            prompts.append(kwargs["initial_prompt"])
            return [], None

    fake_module = types.SimpleNamespace(WhisperModel=FakeWhisper)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    playing = {"value": False}
    transcribe = local_transcriber(
        "tiny.en", prompt=lambda: "Kaki, stop." if playing["value"] else None
    )
    transcribe(b"\x00\x00" * 160)
    playing["value"] = True
    transcribe(b"\x00\x00" * 160)
    assert prompts == ["Kaki", "Kaki, stop."]
