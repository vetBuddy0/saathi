"""tools/media.py's `choose_result` (2026-10-08): play the most relevant
result at once; ask only when the top results are one song title by
different artists. Realistic YouTube titles and channel names."""

from saathi.tools.media import (
    CARD_TITLE,
    MediaController,
    MediaResult,
    choose_result,
    song_and_artist,
)
from saathi.screen.cards import CardController


def _results(*pairs: tuple[str, str]) -> list[MediaResult]:
    return [
        MediaResult(index=i + 1, video_id=f"v{i + 1}", title=title, raw_title=title, channel=ch)
        for i, (title, ch) in enumerate(pairs)
    ]


SHAPE_OF_YOU = _results(
    ("Ed Sheeran - Shape of You (Official Music Video)", "Ed Sheeran"),
    ("Shape of You - Ed Sheeran (Lyrics)", "7clouds"),
    ("Ed Sheeran – Shape of You [Official Lyric Video]", "Ed Sheeran"),
)
HELLO = _results(
    ("Adele - Hello (Official Music Video)", "AdeleVEVO"),
    ("Lionel Richie - Hello (Official Music Video)", "LionelRichieVEVO"),
    ("Adele - Hello (Lyrics)", "Dan Music"),
)


def test_one_song_by_one_artist_in_three_versions_plays_the_top_one():
    top, kept = choose_result(SHAPE_OF_YOU, "Shape of You")
    assert top is not None and top.video_id == "v1"
    assert [r.video_id for r in kept] == ["v1"]  # the other versions fold away


def test_the_same_title_by_different_artists_is_asked():
    top, options = choose_result(HELLO, "hello")
    assert top is None
    assert [r.video_id for r in options] == ["v1", "v2"]  # one per artist
    assert [r.index for r in options] == [1, 2]


def test_different_songs_play_the_top_one_and_keep_the_rest_for_next():
    results = _results(
        ("Adele - Hello (Official Music Video)", "AdeleVEVO"),
        ("Adele - Skyfall (Official Lyric Video)", "AdeleVEVO"),
        ("Adele - Someone Like You (Official Music Video)", "AdeleVEVO"),
    )
    top, kept = choose_result(results, "adele songs")
    assert top.video_id == "v1"
    assert [r.video_id for r in kept] == ["v1", "v2", "v3"]


def test_song_and_artist_read_either_order():
    assert song_and_artist(SHAPE_OF_YOU[0], "Shape of You") == ("shape of you", "ed sheeran")
    assert song_and_artist(SHAPE_OF_YOU[1], "Shape of You") == ("shape of you", "ed sheeran")
    assert song_and_artist(SHAPE_OF_YOU[2], "Shape of You") == ("shape of you", "ed sheeran")
    assert song_and_artist(HELLO[1], "hello") == ("hello", "lionel richie")


def test_a_title_with_no_artist_is_sung_by_its_channel():
    results = _results(("Hello", "Adele"), ("Hello (Live)", "Lionel Richie - Topic"))
    top, options = choose_result(results, "hello")
    assert top is None and len(options) == 2


def test_an_unknown_singer_is_not_a_second_singer():
    results = _results(("Ed Sheeran - Shape of You", "Ed Sheeran"), ("Shape of You", ""))
    top, _kept = choose_result(results, "shape of you")
    assert top is not None


def test_the_controller_plays_directly_and_says_nothing():
    sent: list[dict] = []
    media = MediaController(
        search=lambda q: SHAPE_OF_YOU, broadcast=sent.append, cards=CardController(),
        choose=choose_result,
    )
    reply = media.handle("search", query="Shape of You")
    assert reply["status"] == "ok" and reply["say"] == ""
    assert reply["results"]  # the router knows media is in play
    assert [m["action"] for m in sent if m["type"] == "media"] == ["play"]
    assert sent[-1]["video_id"] == "v1"
    assert media.card_id is None  # no card asking
    assert media.handle("next")["status"] == "end_of_results"


def test_the_controller_asks_which_hello():
    sent: list[dict] = []
    cards = CardController()
    cards.set_broadcast(sent.append)
    media = MediaController(search=lambda q: HELLO, broadcast=sent.append, cards=cards,
                            choose=choose_result)
    reply = media.handle("search", query="hello")
    assert "say" not in reply
    assert len(reply["results"]) == 2
    assert cards.current is not None and cards.current.title == CARD_TITLE
    assert not any(m.get("action") == "play" for m in sent)
    assert media.handle("play", choice=2)["playing"].startswith("Two: Lionel Richie")


def test_a_volume_change_is_its_own_answer():
    media = MediaController(search=lambda q: SHAPE_OF_YOU, choose=choose_result)
    media.handle("search", query="Shape of You")
    reply = media.handle("louder")
    assert reply["say"] == ""
    for _ in range(10):
        reply = media.handle("louder")
    assert "say" not in reply and "loud as it goes" in reply["note"]  # said, by the model
