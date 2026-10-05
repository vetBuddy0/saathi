"""voice/router.py: clear commands route to a tool with exact
arguments; near-misses return None so the model sees them."""

import pytest

from saathi.voice.router import Command, Context, route

MEDIA = Context(results_offered=True, results_count=3)
CARD = Context(card_pending=True)


@pytest.mark.parametrize(
    "heard, expected",
    [
        ("Pause.", Command("play_music", {"action": "pause"}, "Paused.")),
        ("hold on", Command("play_music", {"action": "pause"}, "Paused.")),
        ("Carry on.", Command("play_music", {"action": "resume"}, "Carrying on.")),
        ("Play", Command("play_music", {"action": "resume"}, "Carrying on.")),
        ("Stop it.", Command("play_music", {"action": "stop"}, "Stopped.")),
        ("Close YouTube", Command("play_music", {"action": "stop"}, "Stopped.")),
        ("Volume up!", Command("play_music", {"action": "louder"}, None)),
        ("Turn it down, please.", Command("play_music", {"action": "quieter"}, None)),
        ("Make it bigger", Command("play_music", {"action": "bigger"}, "")),
        ("Smaller.", Command("play_music", {"action": "smaller"}, "")),
        ("Another one.", Command("play_music", {"action": "next"}, None)),
        ("Play it again", Command("play_music", {"action": "again"}, None)),
        ("Never mind.", Command("play_music", {"action": "never_mind"}, "")),
        ("The second one.", Command("play_music", {"action": "play", "choice": 2}, None)),
        ("Number three", Command("play_music", {"action": "play", "choice": 3}, None)),
        ("One.", Command("play_music", {"action": "play", "choice": 1}, None)),
        ("Can you play the first one?",
         Command("play_music", {"action": "play", "choice": 1}, None)),
        # The last clause wins.
        ("Pause... no, actually play the second one.",
         Command("play_music", {"action": "play", "choice": 2}, None)),
    ],
)
def test_media_commands_route_when_results_are_on_offer(heard, expected):
    assert route(heard, context=MEDIA) == expected


@pytest.mark.parametrize(
    "heard, contact",
    [
        ("Call my son.", "my son"),
        ("call my daughter", "my daughter"),
        ("Please ring Priya.", "priya"),
        ("Phone the test number", "the test number"),
        ("Saathi, can you call Deepak please", "deepak"),
    ],
)
def test_calling_routes_with_the_contact_as_she_said_it(heard, contact):
    assert route(heard) == Command("call_contact", {"contact": contact}, None)


@pytest.mark.parametrize(
    "heard, context",
    [
        ("Call it a day.", Context()),
        ("Call me later.", Context()),
        ("I'd like to call my son tomorrow.", Context()),
        ("I'd like to stop smoking.", MEDIA),
        ("Louder", Context()),  # nothing on offer: her own voice, the model's call
        ("Pause", Context()),
        ("Two.", Context()),  # a bare number with nothing on offer is an answer, not a pick
        ("Yes.", Context()),
        ("Yes.", MEDIA),
        ("I didn't sleep well.", MEDIA),
        ("Play something cheerful.", MEDIA),
        ("Stop the video and call my son later", Context()),
        ("Hang up.", Context()),
    ],
)
def test_non_commands_and_near_misses_go_to_the_model(heard, context):
    assert route(heard, context=context) is None


def test_a_pending_card_takes_yes_no_and_numbers():
    assert route("Yes.", context=CARD) == Command("answer_card", {"yes": True}, None)
    assert route("No", context=CARD) == Command("answer_card", {"yes": False}, None)
    assert route("The second one", context=CARD) == Command("answer_card", {"choice": 2}, None)


def test_a_single_result_confirm_card_takes_yes():
    context = Context(results_offered=True, results_count=1)
    assert route("yes", context=context) == Command(
        "play_music", {"action": "play", "choice": 1}, None
    )
