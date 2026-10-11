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
    MAX_TITLE_WIDTH,
    MAX_VOLUME,
    MIN_VOLUME,
    RETRY_CARD_TITLE,
    VOLUME_STEP,
    MediaController,
    MediaResult,
    MediaSearchError,
    clean_title,
    make_media_tool,
    parse_search_response,
    playable_video_ids,
    title_width,
    youtube_search,
)
from saathi.tools.registry import PermissionDenied, Registry

FIXTURE = Path(__file__).parent / "fixtures" / "youtube_search_old_chinese_songs.json"
VIDEOS_FIXTURE = Path(__file__).parent / "fixtures" / "youtube_videos_status.json"


@pytest.fixture
def fixture_body() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def videos_body() -> dict:
    return json.loads(VIDEOS_FIXTURE.read_text())


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
    assert title_width(cleaned) <= MAX_TITLE_WIDTH + 1
    assert cleaned.endswith("…")
    assert not cleaned[:-1].endswith(" ")


def test_clean_title_caps_a_cjk_title_by_width_so_it_is_as_short_to_say_as_an_english_one(
    results,
):
    # A CJK character is twice as wide on screen and about a syllable
    # each; the cap is on width, so a Chinese title gets fewer characters.
    assert title_width(results[0].title) <= MAX_TITLE_WIDTH + 1
    assert len(results[0].title) <= MAX_TITLE_WIDTH // 2 + 1
    assert results[0].title.endswith("…")
    assert results[0].title == "推荐50多岁以上的人真正喜欢的歌曲…"  # cut at the space, not mid-run


def test_clean_title_strips_bracketed_junk_symbols_and_boilerplate():
    assert (
        clean_title("Teresa Teng - The Moon Represents My Heart (Official Video) [HD]")
        == "Teresa Teng - The Moon Represents My Heart"
    )
    assert clean_title("月亮代表我的心 - 鄧麗君【高清】 (内附歌詞)") == "月亮代表我的心 - 鄧麗君"
    assert clean_title("♪ Old Songs ♣ 50首 ★ 🎵") == "Old Songs 50首"
    assert clean_title("鄧麗君《月亮代表我的心》官方 MV") == "鄧麗君 月亮代表我的心"
    assert clean_title("Song Title | Lyrics | Official Music Video") == "Song Title"
    assert clean_title("Audiophile Mix ~ 4K") == "Audiophile Mix"  # "audio" inside a word stays


def test_clean_title_cuts_a_track_listing_and_folds_repeated_names(fixture_body):
    titles = [clean_title(item["snippet"]["title"]) for item in fixture_body["items"]]
    assert titles[2] == "鄧麗君傳唱金曲"  # the "(2) (内附歌詞) 01 …；02 …" tail is gone
    assert titles[1] == "The Moon Represents My Heart - Teresa Teng"  # untouched
    assert "♣" not in titles[0] and "李茂山, 李茂山" not in titles[0]
    assert clean_title("林淑容 , 李茂山 , 李茂山", max_width=200) == "林淑容, 李茂山"
    assert clean_title("Top 10 songs of 1980") == "Top 10 songs of 1980"  # one number: no list
    assert clean_title("1. Song A 2. Song B") == "1. Song A 2. Song B"  # a list is all there is


def test_a_results_language_is_the_language_of_its_title(results):
    assert results[0].language == "chinese"
    assert results[1].language == "english"


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


class _Response:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._body).encode()


def _fake_api(monkeypatch, search_body, videos_body):
    """`urlopen` serving the search fixture to `search.list` and the
    status fixture to `videos.list`; returns the URLs asked for."""
    seen: list[str] = []

    def fake_urlopen(url, timeout):
        seen.append(url)
        if url.startswith(media_module._VIDEOS_URL):
            if isinstance(videos_body, Exception):
                raise videos_body
            return _Response(videos_body)
        return _Response(search_body)

    monkeypatch.setattr(media_module.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_search_builds_the_documented_requests(monkeypatch, fixture_body, videos_body):
    seen = _fake_api(monkeypatch, fixture_body, videos_body)
    found = youtube_search("old Chinese songs", api_key="k")
    assert len(seen) == 2
    search, videos = seen
    assert search.startswith("https://www.googleapis.com/youtube/v3/search?")
    assert "maxResults=10" in search  # candidates, before the embeddability check
    assert "type=video" in search
    assert "videoEmbeddable=true" in search
    assert "q=old+Chinese+songs" in search
    assert videos.startswith("https://www.googleapis.com/youtube/v3/videos?")
    assert "part=status%2CcontentDetails" in videos
    for item in fixture_body["items"]:
        assert item["id"]["videoId"] in videos
    assert "key=k" in videos
    # The status fixture says the first search result has embedding
    # disabled: it is dropped, the rest renumbered from one.
    assert [r.video_id for r in found] == ["bv_cEeDlop0", "UB2p17H30ng"]
    assert [r.index for r in found] == [1, 2]


def test_playable_video_ids_keeps_only_what_the_embedded_player_can_play(videos_body):
    assert playable_video_ids(videos_body) == ["bv_cEeDlop0", "UB2p17H30ng", "embeddable2"]
    assert playable_video_ids({}) == []


def test_search_offers_at_most_three_of_the_playable_candidates(monkeypatch, fixture_body):
    fixture_body["items"] = fixture_body["items"] * 4  # twelve candidates, ten asked for
    ids = [item["id"]["videoId"] for item in fixture_body["items"]]
    status = {
        "items": [
            {"id": i, "status": {"embeddable": True, "privacyStatus": "public"}} for i in ids
        ]
    }
    _fake_api(monkeypatch, fixture_body, status)
    found = youtube_search("q", api_key="k")
    assert [r.index for r in found] == [1, 2, 3]


def test_search_keeps_each_results_thumbnail_through_the_status_check(
    monkeypatch, fixture_body, videos_body
):
    # The card shows the picture beside the number; the renumbering
    # after the status check used to rebuild each result without it.
    _fake_api(monkeypatch, fixture_body, videos_body)
    found = youtube_search("q", api_key="k")
    assert found and all(r.thumbnail and r.thumbnail.startswith("https://") for r in found)


def test_search_falls_back_to_unchecked_results_when_the_status_call_fails(
    monkeypatch, fixture_body, caplog
):
    error = urllib.error.HTTPError("https://x", 500, "boom", {}, None)
    seen = _fake_api(monkeypatch, fixture_body, error)
    found = youtube_search("q", api_key="k")
    assert len(seen) == 2
    assert len(found) == 3  # the search's own videoEmbeddable filter is what's left
    assert "unchecked" in caplog.text
    assert "key=k" not in caplog.text


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


# -- the target rule: the embed first, the browser page after it refuses ---


def _watch(result):
    return f"https://www.youtube.com/watch?v={result.video_id}"


def test_a_play_names_the_embed_target_and_the_watch_page(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    play = sent[-1]
    assert play["action"] == "play"
    assert play["target"] == "embed"
    assert play["watch_url"] == _watch(results[1])
    assert controller.target == "embed"


def test_an_embed_refusal_replays_the_same_video_on_the_browser_target_at_once(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id, code=150)
    play = sent[-1]
    assert play["action"] == "play" and play["target"] == "browser"
    assert play["video_id"] == results[1].video_id and play["index"] == 2
    assert play["watch_url"] == _watch(results[1])
    assert play["volume"] == DEFAULT_VOLUME and play["fullscreen"] is False
    # No stop in between: the panel blanked itself when it reported.
    assert sent[-2]["action"] == "play" and sent[-2]["target"] == "embed"
    assert controller.playing is True and controller.target == "browser"
    assert controller.now_playing == results[1]
    assert results[1].video_id in controller.unplayable
    assert results[1].video_id not in controller.refused
    assert controller.last_results == results  # still on offer


def test_again_next_and_resume_take_the_browser_target_once_the_embed_refused(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id, code=101)
    controller.on_browser_event("ended")
    for action in ("resume", "again"):
        reply = controller.handle(action)
        assert reply["status"] == "ok", action
        assert sent[-1]["action"] == "play", action
        assert sent[-1]["target"] == "browser" and sent[-1]["video_id"] == results[1].video_id
    controller.handle("play", choice=1)
    assert sent[-1]["target"] == "embed"  # only the refused one goes to the browser
    controller.handle("next")  # the second, again
    assert sent[-1]["target"] == "browser" and sent[-1]["index"] == 2
    controller.handle("next")
    assert sent[-1]["target"] == "embed" and sent[-1]["index"] == 3


def test_a_play_that_changes_target_stops_the_other_target_first(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.on_browser_event("error", results[0].video_id, code=150)  # now on the browser
    controller.handle("play", choice=2)  # the embed: the watch page must not play on
    assert [m["action"] for m in sent[-2:]] == ["stop", "play"]
    assert sent[-1]["target"] == "embed"
    controller.handle("play", choice=3)  # embed to embed: no stop, as before
    assert [m["action"] for m in sent[-2:]] == ["play", "play"]
    controller.handle("play", choice=1)  # back to the browser: the embed stops first
    assert [m["action"] for m in sent[-2:]] == ["stop", "play"]
    assert sent[-1]["target"] == "browser"
    controller.on_browser_event("ended")
    controller.handle("play", choice=2)  # nothing was playing: nothing to stop
    assert [m["action"] for m in sent[-2:]] == ["play", "play"]


def test_every_error_code_but_the_browsers_own_is_the_embeds(results):
    for code in (150, 101, 100, "no_ready", "api", None):
        controller, sent = _controller(results)
        controller.handle("search", query="q")
        controller.handle("play", choice=1)
        controller.on_browser_event("error", results[0].video_id, code=code)
        assert sent[-1]["action"] == "play" and sent[-1]["target"] == "browser", code
        assert results[0].video_id in controller.unplayable
        assert results[0].video_id not in controller.refused


def test_a_repeated_embed_refusal_does_not_replay_a_second_time(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.on_browser_event("error", results[0].video_id, code=150)
    before = len(sent)
    controller.on_browser_event("error", results[0].video_id, code=150)  # a second face page
    assert len(sent) == before
    assert results[0].video_id not in controller.refused
    # "carry on" sends it to the browser again; nothing loops by itself.
    controller.handle("resume")
    assert sent[-1]["action"] == "play" and sent[-1]["target"] == "browser"


def test_a_late_embed_refusal_for_a_video_she_moved_past_is_remembered_only(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.handle("play", choice=2)  # she moved on before the first one's report landed
    controller.on_browser_event("error", results[0].video_id, code=150)
    assert sent[-1]["video_id"] == results[1].video_id  # nothing re-emitted
    assert controller.playing is True  # the second one is still playing
    assert results[0].video_id in controller.unplayable
    controller.handle("play", choice=1)
    assert sent[-1]["target"] == "browser"


def test_a_browser_failure_refuses_the_video_as_the_embed_used_to(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id, code=150)
    controller.on_browser_event("error", results[1].video_id, code="browser")
    assert controller.playing is False
    assert results[1].video_id in controller.refused

    for action in ("resume", "again", "play"):
        reply = controller.handle(action)
        assert reply["status"] == "unplayable", action
        assert len(reply["results"]) == 2
        assert all(not r.startswith("Two") for r in reply["results"])
    assert sent[-1]["action"] == "play"  # the browser play; nothing re-emitted
    assert sent[-1]["target"] == "browser" and sent[-1]["index"] == 2
    assert controller.handle("play", choice=1)["status"] == "ok"


def test_a_search_still_offers_a_video_the_embed_refused(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id, code=150)
    reply = controller.handle("search", query="q")
    assert controller.last_results == results
    assert len(reply["results"]) == 3
    controller.handle("play", choice=2)
    assert sent[-1]["target"] == "browser"


def test_a_search_drops_a_video_the_browser_failed_on_and_keeps_the_pictures(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id, code=150)
    controller.on_browser_event("error", results[1].video_id, code="wall")
    controller.handle("search", query="q")
    assert [r.video_id for r in controller.last_results] == [
        results[0].video_id,
        results[2].video_id,
    ]
    assert [r.index for r in controller.last_results] == [1, 2]
    assert [r.thumbnail for r in controller.last_results] == [
        results[0].thumbnail,
        results[2].thumbnail,
    ]
    assert all(r.thumbnail for r in controller.last_results)


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


def test_a_browser_refused_video_is_left_off_the_next_offer_and_the_rest_renumbered(results):
    # Found in review: a tap on a result the player had already failed
    # on cleared the card and said nothing. A video no target will play
    # is never offered; one only the embed refused stays (it plays on
    # the browser target).
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id, code=150)  # to the browser
    reply = controller.handle("search", query="q")
    assert [o["n"] for o in sent[-1]["card"]["options"]] == [1, 2, 3]  # still offered
    controller.on_browser_event("error", results[1].video_id, code="wall")  # refused
    reply = controller.handle("search", query="q")
    assert [r.video_id for r in controller.last_results] == [
        results[0].video_id,
        results[2].video_id,
    ]
    assert [r.index for r in controller.last_results] == [1, 2]
    assert [o["n"] for o in sent[-1]["card"]["options"]] == [1, 2]
    assert [o["image"] for o in sent[-1]["card"]["options"]] == [
        results[0].thumbnail,
        results[2].thumbnail,
    ]
    assert reply["results"][1].startswith("Two: ")
    controller.handle("play", choice=2)
    assert sent[-1]["video_id"] == results[2].video_id


def test_without_cards_nothing_changed(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    assert sent[-1]["action"] == "results"
    assert controller.card_id is None
    assert controller.handle("never_mind")["status"] == "ok"


# -- a pick ends the exchange; a failed play is never silent -----------------


def test_play_ends_the_turn_with_nothing_said_and_a_record_of_what_started(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    reply = controller.handle("play", choice=2)
    assert reply["status"] == "ok"
    assert reply["say"] == ""  # cascade.py: the exchange is over, speak nothing
    assert "Two: The Moon Represents My Heart - Teresa Teng" in reply["did"]
    assert json.dumps(reply)
    assert controller.handle("again")["say"] == ""
    assert controller.handle("next")["say"] == ""


def test_the_offer_card_is_the_tools_before_the_screen_is_told(results):
    # A tap can land the instant the card is drawn (TODO M9): the id must
    # already be the tool's, and the dismiss show() reports for the card
    # it replaces must not be mistaken for the new card's answer.
    from saathi.screen.cards import CardController

    seen = {}

    class EagerCards(CardController):
        def show(self, card):
            seen["at_show"] = controller.card_id
            return super().show(card)

    cards = EagerCards()
    sent: list = []
    cards.set_broadcast(sent.append)
    controller = MediaController(search=lambda q: results, broadcast=sent.append, cards=cards)
    controller.handle("search", query="q")
    assert seen["at_show"] == controller.card_id == cards.current.id
    first = controller.card_id
    controller.handle("search", query="q again")  # replaces: the old card's dismiss is ignored
    assert controller.card_id == cards.current.id != first


def test_an_embed_refusal_with_cards_shows_no_card_and_plays_on_the_browser(results, caplog):
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    cards.answer(controller.card_id, {"choice": 2}, source="tap")
    assert sent[-1]["action"] == "play" and sent[-1]["target"] == "embed"
    controller.on_browser_event("error", results[1].video_id, code=150)
    assert results[1].video_id in caplog.text and "150" in caplog.text
    assert sent[-1]["action"] == "play" and sent[-1]["target"] == "browser"
    assert not [m for m in sent if m["type"] == "card" and m["card"] is not None][1:]
    assert cards.current is None and controller.card_id is None
    assert controller.now_playing == results[1] and controller.playing is True
    assert controller.last_results == results


def test_a_video_the_browser_could_not_play_is_reoffered_by_name_not_swallowed(results, caplog):
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    cards.answer(controller.card_id, {"choice": 2}, source="tap")
    controller.on_browser_event("error", results[1].video_id, code=150)
    controller.on_browser_event("error", results[1].video_id, code="browser")
    assert results[1].video_id in caplog.text and "browser" in caplog.text
    card = sent[-1]["card"]
    assert card["kind"] == "choice"
    assert card["title"] == RETRY_CARD_TITLE.format(title=results[1].title)
    assert card["title"] == f"{results[1].title} won't play here. Which instead?"
    assert [o["label"] for o in card["options"]] == [results[0].title, results[2].title]
    assert controller.card_id == card["id"] == cards.current.id
    assert controller.now_playing is None and controller.playing is False
    # A tap on the retry card plays the renumbered second one.
    cards.answer(card["id"], {"choice": 2}, source="tap")
    assert sent[-1]["action"] == "play" and sent[-1]["video_id"] == results[2].video_id
    # The embed refuses that too: the browser gets it, no card yet...
    controller.on_browser_event("error", results[2].video_id, code="no_ready")
    assert sent[-1]["action"] == "play" and sent[-1]["target"] == "browser"
    # ...and the browser fails: one left, offered as a yes/no; then none, no card.
    controller.on_browser_event("error", results[2].video_id, code="wall")
    assert sent[-1]["card"]["kind"] == "confirm"
    assert sent[-1]["card"]["title"] == f"Play {results[0].title}?"
    controller.on_browser_event("error", results[0].video_id, code="browser")
    assert cards.current is None and controller.card_id is None
    assert controller.last_results == []


def test_an_error_for_a_video_not_on_offer_marks_it_and_offers_nothing(results):
    controller, cards, sent = _carded(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.on_browser_event("error", "some-other-video", code=100)
    assert "some-other-video" in controller.unplayable
    assert not [m for m in sent if m["type"] == "card" and m["card"] is not None][1:]


# -- who is there to take a browser play, and whose report is whose ------


def test_without_a_browser_client_an_embed_refusal_is_refused_and_reoffered(results):
    # The Pi kiosk with the face page alone, or a laptop demo: nothing
    # can show the watch page, so the embed's refusal is the browser's
    # verdict too -- the rule from before the target existed. Nothing is
    # re-emitted, and "carry on" does not insist it is playing.
    sent: list = []
    controller = MediaController(
        search=lambda q: results, broadcast=sent.append, browser_available=lambda: False
    )
    controller.handle("search", query="q")
    controller.handle("play", choice=2)
    before = len(sent)
    controller.on_browser_event("error", results[1].video_id, code=150)
    assert len(sent) == before
    assert controller.playing is False
    assert results[1].video_id in controller.refused
    assert results[1].video_id not in controller.unplayable
    reply = controller.handle("resume")
    assert reply["status"] == "unplayable" and len(reply["results"]) == 2
    assert len(sent) == before


def test_the_browser_client_is_asked_for_at_the_moment_of_the_refusal(results):
    present = {"value": True}
    sent: list = []
    controller = MediaController(
        search=lambda q: results, broadcast=sent.append, browser_available=lambda: present["value"]
    )
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.on_browser_event("error", results[0].video_id, code=150)
    assert sent[-1]["action"] == "play" and sent[-1]["target"] == "browser"
    present["value"] = False  # the phone left before the next refusal
    controller.handle("play", choice=2)
    controller.on_browser_event("error", results[1].video_id, code=101)
    assert sent[-1]["action"] == "play" and sent[-1]["target"] == "embed"  # nothing re-emitted
    assert results[1].video_id in controller.refused


def test_without_a_browser_client_the_retry_card_comes_back_as_it_used_to(results):
    from saathi.screen.cards import CardController

    sent: list = []
    cards = CardController(broadcast=sent.append)
    controller = MediaController(
        search=lambda q: results,
        broadcast=sent.append,
        cards=cards,
        browser_available=lambda: False,
    )
    controller.handle("search", query="q")
    cards.answer(controller.card_id, {"choice": 2}, source="tap")
    controller.on_browser_event("error", results[1].video_id, code="no_ready")
    card = sent[-1]["card"]
    assert card["kind"] == "choice"
    assert card["title"] == f"{results[1].title} won't play here. Which instead?"
    assert [o["label"] for o in card["options"]] == [results[0].title, results[2].title]
    assert controller.playing is False and controller.now_playing is None


def test_a_face_page_reset_while_the_browser_target_plays_changes_nothing(results):
    # The face page sends `reset` on every socket open it has no player
    # for -- including a reconnect while the shell's watch page plays.
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.on_browser_event("error", results[0].video_id, code=150)  # now on the browser
    assert controller.playing is True and controller.target == "browser"
    controller.on_browser_event("reset")  # no target: the face page's
    controller.on_browser_event("reset", target="embed")
    assert controller.playing is True
    assert controller.handle("pause")["status"] == "ok"
    assert sent[-1]["action"] == "pause"
    # The shell's own reset (it restarted) is the one that counts here.
    controller.on_browser_event("reset", target="browser")
    assert controller.playing is False and controller.paused is False
    controller.handle("resume")
    assert sent[-1]["action"] == "play" and sent[-1]["target"] == "browser"


def test_a_shell_reset_while_the_embed_plays_changes_nothing(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.on_browser_event("reset", target="browser")
    controller.on_browser_event("reset", target="something-new")  # unknown: the face page's
    assert controller.playing is False  # the second one counted, as a face-page reset does
    controller.handle("play", choice=1)
    controller.on_browser_event("reset", target="browser")
    assert controller.playing is True
    # With nothing playing, any reset is harmless and idempotent.
    controller.handle("stop")
    controller.on_browser_event("reset", target="browser")
    controller.on_browser_event("reset")
    assert controller.playing is False and controller.target == "embed"


def test_an_ended_naming_another_video_is_a_late_report(results):
    controller, sent = _controller(results)
    controller.handle("search", query="q")
    controller.handle("play", choice=1)
    controller.handle("play", choice=2)
    controller.on_browser_event("ended", results[0].video_id)  # the first one, long gone
    assert controller.playing is True
    controller.on_browser_event("ended", results[1].video_id)
    assert controller.playing is False
