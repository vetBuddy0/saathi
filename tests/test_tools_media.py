"""saathi/tools/media.py — `play_music` made real. The thing these tests
protect first: search results stay referenceable for the rest of the
conversation ("the second one", "that one", "play that again").
"""

import json
import urllib.error
from pathlib import Path

import pytest

from saathi.tools import media as media_module
from saathi.tools.media import (
    DEFAULT_VOLUME,
    MAX_TITLE_CHARS,
    MAX_VOLUME,
    MIN_VOLUME,
    VOLUME_STEP,
    MediaController,
    MediaResult,
    MediaSearchError,
    clean_title,
    make_media_tool,
    parse_search_response,
    youtube_search,
)
from saathi.tools.registry import PermissionDenied, Registry

FIXTURE = Path(__file__).parent / "fixtures" / "youtube_search_old_chinese_songs.json"


@pytest.fixture
def fixture_body() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def results(fixture_body) -> list[MediaResult]:
    return parse_search_response(fixture_body)


def _controller(results, sent=None):
    sent = sent if sent is not None else []
    return MediaController(search=lambda q: results, broadcast=sent.append), sent


# -- parsing the real response ------------------------------------------


def test_fixture_has_no_api_key_in_it():
    text = FIXTURE.read_text()
    assert "AIza" not in text
    assert "key=" not in text


def test_parse_real_response_yields_three_numbered_embeddable_videos(results):
    assert [r.index for r in results] == [1, 2, 3]
    assert all(r.video_id for r in results)
    assert results[1].title == "The Moon Represents My Heart - Teresa Teng"
    assert results[1].spoken == "Two: The Moon Represents My Heart - Teresa Teng"


def test_parse_never_yields_more_than_three(fixture_body):
    fixture_body["items"] = fixture_body["items"] * 3
    assert len(parse_search_response(fixture_body)) == 3


def test_parse_skips_items_without_a_video_id(fixture_body):
    fixture_body["items"][0]["id"] = {"kind": "youtube#channel", "channelId": "x"}
    parsed = parse_search_response(fixture_body)
    assert len(parsed) == 2
    assert parsed[0].index == 1  # renumbered from one, not left with a gap


def test_clean_title_unescapes_entities_collapses_whitespace_and_trims():
    assert clean_title("Rock &amp; Roll  \n Hits &#39;60s") == "Rock & Roll Hits '60s"
    long = "word " * 40
    cleaned = clean_title(long)
    assert len(cleaned) <= MAX_TITLE_CHARS + 1
    assert cleaned.endswith("…")
    assert not cleaned[:-1].endswith(" ")


def test_clean_title_trims_a_long_cjk_title_without_a_word_boundary(results):
    assert len(results[0].title) <= MAX_TITLE_CHARS + 1
    assert results[0].title.endswith("…")


# -- the search call -----------------------------------------------------


def test_search_without_a_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(MediaSearchError):
        youtube_search("anything")


def test_search_http_error_is_a_plain_reason_and_never_logs_the_url(monkeypatch, caplog):
    def fail(url, timeout):
        assert "key=sekrit" in url
        raise urllib.error.HTTPError(url, 403, "quota", {}, None)

    monkeypatch.setattr(media_module.urllib.request, "urlopen", fail)
    with pytest.raises(MediaSearchError):
        youtube_search("x", api_key="sekrit")
    assert "sekrit" not in caplog.text
    assert "403" in caplog.text


def test_search_builds_the_documented_request(monkeypatch, fixture_body):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(fixture_body).encode()

    def fake_urlopen(url, timeout):
        seen["url"] = url
        return Response()

    monkeypatch.setattr(media_module.urllib.request, "urlopen", fake_urlopen)
    found = youtube_search("old Chinese songs", api_key="k")
    assert len(found) == 3
    url = seen["url"]
    assert url.startswith("https://www.googleapis.com/youtube/v3/search?")
    assert "maxResults=3" in url
    assert "type=video" in url
    assert "videoEmbeddable=true" in url
    assert "q=old+Chinese+songs" in url


# -- the controller: results stay referenceable -----------------------------


def test_search_broadcasts_results_and_returns_spoken_titles_with_a_note(results):
    controller, sent = _controller(results)
    reply = controller.handle("search", query="old Chinese songs")

    assert reply["status"] == "ok"
    assert reply["results"][0].startswith("One: ")
    assert reply["results"][1].startswith("Two: ")
    assert reply["results"][2].startswith("Three: ")
    assert "note" in reply
    assert json.dumps(reply)  # what cascade.py feeds back must serialize

    assert len(sent) == 1
    assert sent[0]["type"] == "media"
    assert sent[0]["action"] == "results"
    assert [r["index"] for r in sent[0]["results"]] == [1, 2, 3]
    assert sent[0]["results"][1]["video_id"] == results[1].video_id


def test_the_second_one_resolves_against_the_last_search(results):
    controller, sent = _controller(results)
    controller.handle("search", query="old Chinese songs")
    reply = controller.handle("play", choice=2)

    assert reply["status"] == "ok"
    assert reply["playing"].startswith("Two: ")
    play = sent[-1]
    assert play["action"] == "play"
    assert play["video_id"] == results[1].video_id
    assert play["index"] == 2
    assert play["volume"] == DEFAULT_VOLUME


def test_results_stay_referenceable_across_later_turns(results):
    # The brief's protect-this item: several turns later, "the first
    # one" still means the first of what was offered.
    controller, sent = _controller(results)
    controller.handle("search", query="old Chinese songs")
    controller.handle("play", choice=2)
    controller.handle("louder")
    controller.handle("pause")
    controller.handle("resume")
    reply = controller.handle("play", choice=1)
    assert reply["playing"].startswith("One: ")
    assert sent[-1]["video_id"] == results[0].video_id


def test_that_one_with_no_choice_means_the_first_offered_then_the_one_last_played(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play")
    assert sent[-1]["video_id"] == results[0].video_id
    controller.handle("play", choice=3)
    controller.handle("play")  # "that one" again
    assert sent[-1]["video_id"] == results[2].video_id


def test_play_that_again_replays_what_last_played_even_after_it_ended(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=3)
    controller.on_browser_event("ended")
    assert controller.playing is False
    reply = controller.handle("again")
    assert reply["status"] == "ok"
    assert sent[-1]["action"] == "play"
    assert sent[-1]["video_id"] == results[2].video_id


def test_next_walks_the_offered_results_and_stops_at_the_end(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("next")
    assert sent[-1]["index"] == 1
    controller.handle("next")
    assert sent[-1]["index"] == 2
    controller.handle("next")
    assert sent[-1]["index"] == 3
    reply = controller.handle("next")
    assert reply["status"] == "end_of_results"
    assert sent[-1]["index"] == 3  # nothing new was broadcast


def test_a_choice_that_was_never_offered_is_refused_with_the_real_list(results):
    controller, sent = _controller(results[:2])
    controller.handle("search", query="q")
    reply = controller.handle("play", choice=3)
    assert reply["status"] == "no_such_choice"
    assert len(reply["results"]) == 2
    assert all(m["action"] != "play" for m in sent)


def test_play_before_any_search_asks_instead_of_broadcasting(results):
    controller, sent = _controller(results)
    reply = controller.handle("play", choice=1)
    assert reply["status"] == "nothing_to_play"
    assert sent == []


def test_a_new_search_stops_what_was_playing(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.handle("search", query="something else")
    assert controller.playing is False
    assert sent[-1]["action"] == "results"


def test_search_with_no_results_says_so_and_keeps_the_previous_list():
    controller, sent = _controller([])
    reply = controller.handle("search", query="zzzz")
    assert reply["status"] == "none"
    assert sent == []


def test_search_when_youtube_is_unavailable_is_a_plain_reason_not_an_exception():
    def failing(_query):
        raise MediaSearchError("YouTube couldn't be reached just now.")

    controller = MediaController(search=failing, broadcast=lambda m: None)
    reply = controller.handle("search", query="q")
    assert reply["status"] == "unavailable"
    assert "reached" in reply["note"]


def test_search_with_an_empty_query_is_an_error_without_a_call():
    calls = []
    controller = MediaController(search=lambda q: calls.append(q) or [], broadcast=None)
    assert controller.handle("search", query="  ")["status"] == "error"
    assert calls == []


# -- transport controls ---------------------------------------------------


def test_pause_resume_stop_broadcast_their_actions(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    assert controller.handle("pause")["status"] == "ok"
    assert sent[-1] == {"type": "media", "action": "pause"}
    assert controller.paused is True
    assert controller.handle("resume")["status"] == "ok"
    assert sent[-1] == {"type": "media", "action": "resume"}
    assert controller.handle("stop")["status"] == "ok"
    assert sent[-1] == {"type": "media", "action": "stop"}
    assert controller.playing is False


def test_pause_with_nothing_playing_does_not_broadcast(results):
    controller, sent = _controller(results)
    assert controller.handle("pause")["status"] == "nothing_playing"
    assert sent == []


def test_carry_on_after_it_ended_starts_it_over(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("ended")
    controller.handle("resume")
    assert sent[-1]["action"] == "play"
    assert sent[-1]["index"] == 2


def test_louder_and_quieter_step_and_clamp(results):
    controller, sent = _controller(results)
    assert controller.handle("louder")["volume"] == DEFAULT_VOLUME + VOLUME_STEP
    assert sent[-1] == {"type": "media", "action": "volume", "level": DEFAULT_VOLUME + VOLUME_STEP}
    for _ in range(10):
        reply = controller.handle("louder")
    assert reply["volume"] == MAX_VOLUME
    assert "loud as it goes" in reply["note"]
    for _ in range(10):
        reply = controller.handle("quieter")
    assert reply["volume"] == MIN_VOLUME  # never all the way to silent
    assert "quiet as it goes" in reply["note"]


def test_bigger_and_smaller_broadcast_layout_and_stick_for_the_next_play(results):
    controller, sent = _controller(results)
    controller.handle("bigger")
    assert sent[-1] == {"type": "media", "action": "layout", "mode": "fullscreen"}
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    assert sent[-1]["fullscreen"] is True
    controller.handle("smaller")
    assert sent[-1] == {"type": "media", "action": "layout", "mode": "panel"}


def test_without_a_broadcast_seam_actions_still_work_and_drop_the_message(results):
    controller = MediaController(search=lambda q: results)
    assert controller.handle("search", query="q")["status"] == "ok"
    assert controller.handle("play", choice=1)["status"] == "ok"


def test_unknown_action_is_an_error(results):
    controller, sent = _controller(results)
    assert controller.handle("dance")["status"] == "error"
    assert sent == []


# -- the Tool: same name and gate as the stub it replaces ------------------


def test_tool_is_play_music_gated_on_music_and_denied_without_it(results):
    controller, sent = _controller(results)
    registry = Registry()
    registry.register(make_media_tool(controller))

    with pytest.raises(PermissionDenied):
        registry.call("play_music", frozenset({"preferences"}), action="search", query="q")
    assert sent == []

    reply = registry.call("play_music", frozenset({"music"}), action="search", query="q")
    assert reply["status"] == "ok"
    assert sent[-1]["action"] == "results"


def test_tool_schema_lists_every_action_and_only_requires_it(results):
    tool = make_media_tool(_controller(results)[0])
    assert tool.name == "play_music"
    assert tool.permission == "music"
    assert tool.schema["required"] == ["action"]
    assert set(tool.schema["properties"]["action"]["enum"]) == set(media_module.ACTIONS)


def test_tool_accepts_a_choice_sent_as_a_string(results):
    controller, sent = _controller(results)
    tool = make_media_tool(controller)
    tool.handler(action="search", query="q")
    tool.handler(action="play", choice="2")
    assert sent[-1]["index"] == 2


def test_tool_survives_the_models_stray_or_mistyped_arguments(results):
    # A TypeError here would turn the turn into server.py's "I didn't
    # quite catch that" fallback; every odd shape must come back as a
    # status dict instead.
    controller, sent = _controller(results)
    tool = make_media_tool(controller)
    assert tool.handler(action="louder", amount=15)["status"] == "ok"
    # A non-string query is coerced and searched, not a crash. (The fake
    # search ignores the query, so the status is "ok"; the coercion is
    # what's under test.)
    assert tool.handler(action="search", query=5)["status"] == "ok"
    assert controller.last_query == "5"
    tool.handler(action="search", query="q")
    assert tool.handler(action="play", choice=2.0)["playing"].startswith("Two")
    assert tool.handler(action="play", choice="second")["playing"].startswith("Two")  # last meant
    assert tool.handler(action="play", choice=True)["status"] == "ok"
    assert tool.handler(action="play", video_id="abc")["status"] == "ok"
    assert tool.handler()["status"] == "error"
    assert tool.handler(action=3)["status"] == "error"
    for message in sent:
        json.dumps(message)


def test_results_message_carries_the_spoken_label(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    assert [r["label"] for r in sent[-1]["results"]] == ["One", "Two", "Three"]


def test_a_video_the_player_could_not_play_is_not_offered_again(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id)
    assert controller.playing is False

    for action in ("resume", "again", "play"):
        reply = controller.handle(action)
        assert reply["status"] == "unplayable", action
        assert len(reply["results"]) == 2
        assert all(not r.startswith("Two") for r in reply["results"])
    assert sent[-1]["action"] == "play"  # the original play; nothing re-emitted
    assert sent[-1]["index"] == 2
    assert controller.handle("play", choice=1)["status"] == "ok"


def test_an_error_without_a_video_id_marks_what_was_playing(results):
    controller, _ = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=3)
    controller.on_browser_event("error")
    assert results[2].video_id in controller.unplayable


def test_a_reloaded_page_reset_means_carry_on_starts_it_again(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.handle("louder")
    controller.on_browser_event("reset")
    assert controller.playing is False
    assert controller.now_playing == results[0]  # the memory survives
    reply = controller.handle("resume")
    assert reply["status"] == "ok"
    assert sent[-1]["action"] == "play"
    assert sent[-1]["volume"] == DEFAULT_VOLUME + VOLUME_STEP  # the level is re-sent


def test_search_rejects_a_non_object_body(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"[]"

    monkeypatch.setattr(media_module.urllib.request, "urlopen", lambda url, timeout: Response())
    with pytest.raises(MediaSearchError):
        youtube_search("x", api_key="k")


# -- with cards: the offer is a Choice card, answered by tap or voice -------


def _carded(results):
    from saathi.screen.cards import CardController

    sent = []
    cards = CardController(broadcast=sent.append)
    controller = MediaController(search=lambda q: results, broadcast=sent.append, cards=cards)
    return controller, cards, sent


def test_with_cards_a_search_shows_a_choice_card_instead_of_the_panels_results(results):
    controller, cards, sent = _carded(results)
    reply = controller.handle("search", query="old Chinese songs")

    assert [m["type"] for m in sent] == ["card"]  # no media "results" message
    card = sent[0]["card"]
    assert card["kind"] == "choice"
    assert card["id"] == controller.card_id == cards.current.id
    assert [o["label"] for o in card["options"]] == [r.title for r in results]
    assert [o["n"] for o in card["options"]] == [1, 2, 3]
    assert reply["status"] == "ok"
    assert reply["spoken"] == card["spoken"]
    assert card["spoken"].startswith("Which one would you like? One: ")
    assert card["spoken"] in reply["note"]
    assert reply["results"][1].startswith("Two: ")
    assert json.dumps(reply)


def test_a_tap_on_the_card_plays_that_result_and_clears_the_card(results):
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    card_id = controller.card_id
    assert cards.answer(card_id, {"choice": 2}, source="tap") is True
    assert controller.card_id is None
    assert cards.current is None
    plays = [m for m in sent if m["type"] == "media" and m["action"] == "play"]
    assert len(plays) == 1
    assert plays[0]["video_id"] == results[1].video_id
    assert controller.now_playing == results[1]
    assert sent[-1]["action"] == "play"  # the play follows the card's clear


def test_the_second_one_by_voice_answers_the_card_and_plays_once(results):
    controller, cards, sent = _carded(results)
    answers = []
    cards.on_answer(answers.append)
    controller.handle("search", query="q")
    card_id = controller.card_id
    reply = controller.handle("play", choice=2)
    assert reply["status"] == "ok"
    assert cards.current is None and controller.card_id is None
    assert [(a.card_id, a.choice, a.source) for a in answers] == [(card_id, 2, "voice")]
    plays = [m for m in sent if m["type"] == "media" and m["action"] == "play"]
    assert len(plays) == 1  # the callback did not start it a second time
    assert plays[0]["index"] == 2


def test_never_mind_by_voice_or_tap_clears_the_card_and_keeps_the_results(results):
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    reply = controller.handle("never_mind")
    assert reply["status"] == "ok"
    assert cards.current is None and controller.card_id is None
    assert sent[-1] == {"type": "card", "card": None}
    assert not [m for m in sent if m["type"] == "media"]
    # "actually, the second one" a moment later still works
    controller.handle("play", choice=2)
    assert sent[-1]["action"] == "play" and sent[-1]["index"] == 2

    controller.handle("search", query="q")
    card_id = controller.card_id
    cards.answer(card_id, {"dismiss": True}, source="tap")
    assert controller.card_id is None
    assert controller.last_results == results
    assert controller.handle("never_mind")["status"] == "ok"  # nothing up: harmless


def test_a_new_search_replaces_the_card_and_stops_the_player(results):
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    first = controller.card_id
    controller.handle("play", choice=1)
    controller.handle("search", query="something else")
    assert controller.card_id != first
    assert cards.current.id == controller.card_id
    stops = [m for m in sent if m["type"] == "media" and m["action"] == "stop"]
    assert len(stops) == 1
    assert controller.playing is False


def test_stop_and_again_settle_the_card(results):
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    controller.handle("stop")
    assert cards.current is None and controller.card_id is None
    controller.handle("play", choice=3)
    controller.handle("search", query="q")
    controller.handle("again")  # replays 3, which is on the new card: answered as choice 3
    assert cards.current is None
    assert sent[-1]["action"] == "play" and sent[-1]["index"] == 3


def test_an_answer_to_someone_elses_card_is_ignored_by_the_media_tool(results):
    from saathi.screen.cards import confirm

    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    media_card = controller.card_id
    other = confirm("Call Priya?")
    cards.show(other)  # replaces the media card: media forgets it
    assert controller.card_id is None
    cards.answer(other.id, {"yes": True}, source="tap")
    assert not [m for m in sent if m["type"] == "media" and m["action"] == "play"]
    assert media_card != other.id


def test_a_tap_with_a_stale_choice_does_not_play(results):
    controller, cards, sent = _carded(results[:2])
    controller.handle("search", query="q")
    assert cards.answer(controller.card_id, {"choice": 3}, source="tap") is False
    assert not [m for m in sent if m["type"] == "media"]


def test_a_single_result_is_offered_as_a_confirm_card_not_a_choice_of_one(results):
    # Found in review: choice() with one option raised and took the turn
    # down. One result is a yes/no.
    controller, cards, sent = _carded(results[:1])
    reply = controller.handle("search", query="q")
    assert reply["status"] == "ok"
    card = sent[-1]["card"]
    assert card["kind"] == "confirm"
    assert card["title"] == f"Play {results[0].title}?"
    assert reply["spoken"] == card["spoken"]

    # Tapping "Yes" plays it; the card goes.
    assert cards.answer(card["id"], {"yes": True}, source="tap") is True
    assert sent[-1]["action"] == "play" and sent[-1]["index"] == 1
    assert controller.card_id is None

    # "No" leaves it referenceable; "play it" by voice answers yes.
    controller.handle("search", query="q")
    cards.answer(controller.card_id, {"yes": False}, source="tap")
    assert controller.playing is False
    controller.handle("search", query="q")
    answers = []
    cards.on_answer(answers.append)
    controller.handle("play")
    assert answers[-1].yes is True and answers[-1].source == "voice"
    assert sent[-1]["action"] == "play"
    assert len([m for m in sent if m.get("action") == "play"]) == 2


def test_an_unplayable_video_is_left_off_the_next_offer_and_the_rest_renumbered(results):
    # Found in review: a tap on a result the player had already failed
    # on cleared the card and said nothing. Now it's never offered.
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id)
    reply = controller.handle("search", query="q")
    assert [r.video_id for r in controller.last_results] == [
        results[0].video_id,
        results[2].video_id,
    ]
    assert [r.index for r in controller.last_results] == [1, 2]
    assert [o["n"] for o in sent[-1]["card"]["options"]] == [1, 2]
    assert reply["results"][1].startswith("Two: ")
    controller.handle("play", choice=2)
    assert sent[-1]["video_id"] == results[2].video_id


def test_without_cards_nothing_changed(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    assert sent[-1]["action"] == "results"
    assert controller.card_id is None
    assert controller.handle("never_mind")["status"] == "ok"
