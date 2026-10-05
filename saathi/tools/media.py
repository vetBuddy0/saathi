"""`play_music` made real: YouTube search and playback, driven by speech
and the spacebar alone.

SPEC.md put music out of v1 and left `stubs.py`'s `play_music` so "v2
swaps an implementation rather than inventing plumbing". This is that
swap. It keeps the same `Tool` shape, the same registry, the same
`"music"` permission gate, and replaces the handler. The stub itself is
left untouched (it is still what `stubs.register()` installs); `cli.py`
registers this tool instead.

Why a controller object and not a bare handler: search results have to
stay referenceable for the rest of the conversation. "Play the second
one", "that one", "play that again" only work if something remembers
what was offered and what last played, across turns, on the same object
the handler closes over. `MediaController.last_results` is that memory.
It is the one thing this module protects if everything else slips: a
search box forgets what it showed; a person doesn't.

Why one tool with an `action` enum rather than ten tools: `cascade.py`
handles exactly one tool call per turn (DECISIONS 2026-09-18), so
"search, then offer the results out loud" has to be a single call whose
returned dict carries the titles phrased for speech. Ten tools would
have meant ten descriptions competing for the model's choice on every
turn; one tool with a small vocabulary that matches how she talks
(`louder`, `bigger`, `again`) is what the model picks reliably.

Why the tool never touches the browser socket: the handler runs on
`server.py`'s executor thread, and the socket set is only safe on the
event-loop thread. The controller emits a JSON-serializable message
through a `broadcast` callable that `screen/server.py` owns and installs
via `set_broadcast()`; the server dispatches it to the loop. CLAUDE.md:
a tool reaching into the UI silently removes the architecture — so the
seam is the server's, and this module only calls it. Nothing here
decides state either: playback state (playing, paused, volume, layout)
is media state, not `core.py` state, and the face is driven by core
alone.

Why the official Data API and IFrame player, not yt-dlp: yt-dlp works
and breaches YouTube's terms, so it cannot ship on a device sold to a
family. The Data API costs 100 quota units per search out of 10,000 a
day — ample for one person, and this module never loops on it. The key
is read from the environment (`YOUTUBE_API_KEY`), same precedent as
`GROQ_API_KEY`; it is never read from a file in the repo and never
logged.

Why `urllib` and not `aiohttp` here: the handler is synchronous by
contract and already off the loop thread; a blocking GET is the simple,
correct thing. Spinning up an event loop inside the executor to use
aiohttp would be plumbing for its own sake.

Why a search is two requests, not one (2026-09-26): `search.list`'s
`videoEmbeddable=true` is a hint the API does not honour reliably --
the first live run offered a video whose owner had disabled embedding,
and the player failed on it with nothing on screen to say why. The
search now asks for ten candidates and checks them with one
`videos.list?part=status,contentDetails` call (one quota unit), keeping
only videos that report `embeddable`, aren't private and aren't
age-restricted (an age-restricted embed demands a sign-in the kiosk
can't give), then offers the first three. If the status call itself
fails, the search's own filter is what's left and the fallback is
logged, not hidden.

Why a play ends the turn with nothing said (2026-09-26): "the second
one" used to come back through the model with a note asking for one
short sentence, and the model kept talking over the start of the song.
Once she has picked, the only right answer is to play it; the tool's
result now carries `say: ""`, which `cascade.py` treats as "the
exchange is over, speak nothing" -- no second model call, no sentence.
The exchange is still recorded (`did`) so the next turn knows what is
playing. A tap needs nothing here: `screen/server.py` ends the turn
that was still reading the options when the tap lands.

Why titles are cleaned as hard as they are (2026-09-26): the first live
search returned "推荐50多岁以上的人真正喜欢的歌曲 ♣ 50首70、80、90年代唱遍
大街小巷的歌曲今天给大家推荐 , 林淑容 , 李茂山 , 李茂山" and a track listing
with semicolons, read in full in the English voice. `clean_title` drops
bracketed runs, symbols and emoji, "(Official Video)"-style boilerplate
in English and Chinese, track lists, and repeated segments, then caps
the *width* (a CJK character counts double, as it does on screen and in
the ear). Which voice reads a title is decided per sentence by script in
`cascade.py` (`voice/language.py`'s `script_language`), so a Chinese
title in an English reply is read by the Chinese voice.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from saathi.screen.cards import Answer, Card, CardController, choice, confirm
from saathi.tools.registry import Tool
from saathi.voice.language import script_language

logger = logging.getLogger(__name__)

# Three, never more. Read aloud and shown; a fourth would be a list.
MAX_RESULTS = 3
# How many candidates a search asks for before the embeddability check
# (see the module docstring). Ten is enough that three usually survive
# and few enough to stay one `videos.list` call.
SEARCH_CANDIDATES = 10
# Titles on YouTube run long. Trimmed for both screen and speech so a
# line stays a line and the model doesn't read a paragraph. Measured in
# display width: a CJK character counts 2, everything else 1, so a
# Chinese title is capped at about thirty characters and an English one
# at sixty -- roughly the same number of syllables either way.
MAX_TITLE_WIDTH = 60

# Volume is tracked here, not in the browser, so "louder" is
# deterministic and testable and so a reconnected browser can be told
# the level again. 70 is comfortably audible on the built-in speaker
# without being the room; steps of 15 give four "louder"s from the
# floor to the ceiling. The floor is 10, not 0 -- "quieter" should never
# silence it; "stop" does that.
DEFAULT_VOLUME = 70
VOLUME_STEP = 15
MIN_VOLUME = 10
MAX_VOLUME = 100

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_HTTP_TIMEOUT_SECONDS = 8.0

ACTIONS = (
    "search",
    "play",
    "pause",
    "resume",
    "stop",
    "louder",
    "quieter",
    "bigger",
    "smaller",
    "again",
    "next",
    "never_mind",
)

CARD_TITLE = "Which one would you like?"
# The offer again, after the player reported it could not play the one
# she picked. Shown by the controller between turns, so nothing speaks
# it; the model learns what happened from the next tool call.
RETRY_CARD_TITLE = "That one won't play here. Which instead?"

ORDINALS = {1: "One", 2: "Two", 3: "Three"}

MEDIA_DESCRIPTION = (
    "Play music or videos from YouTube on the screen next to your face. "
    "Use action 'search' with a query when she asks to hear something ('play some "
    "old Chinese songs', 'search for Teresa Teng', 'something else'); the query is "
    "her own words for what she wants, not your guess at something more specific. "
    "It finds up to three results, shows them on screen, and returns their titles "
    "for you to offer out loud by number. Use 'play' with choice 1, 2 or 3 when she "
    "picks one ('the second one', 'the first one'); 'play' with no choice means the one she most "
    "recently meant ('that one'). 'again' replays what last played. 'next' plays "
    "the next result. 'never_mind' when she waves the offered choices away without "
    "picking one. 'pause', 'resume' ('carry on'), 'stop', 'louder', 'quieter', "
    "'bigger' (fill the screen) and 'smaller' (back beside your face) do what "
    "they say. Never invent a title: only offer what the tool returned."
)


@dataclass(frozen=True)
class MediaResult:
    index: int  # 1-based, the number she hears and sees
    video_id: str
    title: str

    def as_message(self) -> dict[str, Any]:
        # `label` is the word she hears ("Two"), sent so the screen shows
        # exactly what is spoken without deriving it a second time.
        return {
            "index": self.index,
            "label": ORDINALS[self.index],
            "video_id": self.video_id,
            "title": self.title,
        }

    @property
    def spoken(self) -> str:
        return f"{ORDINALS[self.index]}: {self.title}"

    @property
    def language(self) -> str:
        """The language the title is written in, by script: what the
        voice that reads it should speak."""
        return script_language(self.title, "english")


class MediaSearchError(RuntimeError):
    """The search could not be done -- no key, network, quota, bad
    response. Carries a plain-language reason the tool result can hand
    the model; the technical detail goes to the log."""


# -- titles ---------------------------------------------------------------

# Bracketed runs, ASCII and full-width: "(Official Video)", "[MV]",
# "【高清】", "（内附歌詞）". Dropped with their contents. Quote-like marks
# (《》「」『』) wrap the song's own name in Chinese titles, so only the
# marks go and the words stay.
_BRACKETED = re.compile(r"[(\[{（［【〔]\s*[^()\[\]{}（）［］【】〔〕]*?\s*[)\]}）］】〕]")
_QUOTE_MARKS = re.compile(r"[《》〈〉「」『』“”\"]")
_LATIN_BOILERPLATE = re.compile(
    r"\b("
    r"official\s+(?:music\s+)?(?:video|audio|mv|lyrics?\s+video)|official|"
    r"lyrics?\s+video|with\s+lyrics|lyrics|"
    r"mv|hd|hq|4k|8k|1080p|720p|"
    r"full\s+(?:album|song|version)|remaster(?:ed)?|high\s+quality"
    r")\b",
    re.IGNORECASE,
)
_CJK_BOILERPLATE = re.compile(
    r"動態歌詞|动态歌词|附歌詞|附歌词|歌詞版|歌词版|字幕版|歌詞|歌词|字幕|"
    r"高音質|高音质|無損|无损|純音樂|纯音乐|完整版|高清|超清|官方"
)
# A track listing: "01 你怎麽説；02 小城故事；03 …", "1. Song 2. Song". A
# one- or two-digit number, optional mark, whitespace, then something
# that isn't another digit -- "50首70、80、90年代" has no whitespace after
# its numbers and is left alone. Two markers make a list; the title is
# cut before the first one, unless the list is all there is.
_TRACK_MARKER = re.compile(r"(?<![\w\d])(?:0\d|[1-9]\d?)\s*[.、:：)]?\s+(?=[^\d\s])")
_SEPARATORS = ",，、;；:："
# Where a capped title may be cut: a space or a clause break, not "、",
# which only separates items in a run ("70、80、90年代").
_STRONG_SEPARATORS = ",，;；:：。"
_KEEP_PUNCTUATION = set("-,.'&:!?" + _SEPARATORS + "。")


def _strip_symbols(text: str) -> str:
    """Letters, digits, combining marks and spaces stay; so does the
    punctuation a title needs ("Rock & Roll", "37.5", "Teresa - Live").
    Everything else -- ♣, ♪, emoji, |, /, ~, #, * -- becomes a space."""
    out = []
    for ch in text:
        category = unicodedata.category(ch)
        if category[0] in "LNM" or category == "Zs" or ch in _KEEP_PUNCTUATION:
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def _dedupe_segments(text: str) -> str:
    """"林淑容 , 李茂山 , 李茂山" -> "林淑容, 李茂山"."""
    parts = [p.strip() for p in re.split(r"\s*[,，;；]\s*", text)]
    kept: list[str] = []
    for part in parts:
        if part and (not kept or part != kept[-1]):
            kept.append(part)
    return ", ".join(kept)


def title_width(text: str) -> int:
    """Display width: a wide (CJK) character counts 2."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _cap_width(text: str, max_width: int) -> str:
    if title_width(text) <= max_width:
        return text
    width = 0
    cut = 0
    for i, ch in enumerate(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width > max_width:
            break
        cut = i + 1
    head = text[:cut]
    # Prefer a boundary (a space or a separator) in the second half so a
    # word or a name isn't sliced through; CJK runs have no spaces and a
    # hard cut is fine there.
    boundary = max(head.rfind(ch) for ch in " " + _STRONG_SEPARATORS)
    if boundary > 0 and title_width(head[:boundary]) >= max_width // 2:
        head = head[:boundary]
    return head.rstrip(" " + _SEPARATORS + "。-") + "…"


def clean_title(raw: str, max_width: int = MAX_TITLE_WIDTH) -> str:
    """A YouTube title as a person would say it: entities unescaped,
    bracketed junk, symbols, boilerplate and track lists gone, repeats
    folded, whitespace collapsed, width capped at a boundary."""
    text = html.unescape(raw or "")
    text = _BRACKETED.sub(" ", text)
    text = _QUOTE_MARKS.sub(" ", text)
    text = _LATIN_BOILERPLATE.sub(" ", text)
    text = _CJK_BOILERPLATE.sub(" ", text)
    text = _strip_symbols(text)
    markers = list(_TRACK_MARKER.finditer(text))
    if len(markers) >= 2 and text[: markers[0].start()].strip(" " + _SEPARATORS):
        text = text[: markers[0].start()]
    text = _dedupe_segments(text)
    text = re.sub(r"\s*([,，;；:：])\s*", r"\1 ", text)
    text = re.sub(r"\s*、\s*", "、", text)  # the enumeration comma takes no space
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" " + _SEPARATORS + "-|")
    text = re.sub(r"\s+", " ", text).strip()
    return _cap_width(text, max_width)


# -- the API -----------------------------------------------------------------


def parse_search_response(body: dict[str, Any], limit: int = MAX_RESULTS) -> list[MediaResult]:
    """`search.list` -> at most `limit` `MediaResult`s, numbered from
    one. Anything that isn't a video with an id and a title is skipped,
    not an error."""
    results: list[MediaResult] = []
    for item in body.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        title = clean_title((item.get("snippet") or {}).get("title", ""))
        if not video_id or not title:
            continue
        results.append(MediaResult(index=len(results) + 1, video_id=video_id, title=title))
        if len(results) == limit:
            break
    return results


def playable_video_ids(body: dict[str, Any]) -> list[str]:
    """`videos.list?part=status,contentDetails` -> the ids the embedded
    player can be expected to play: `status.embeddable`, not private,
    not still processing, not age-restricted. A video the response
    doesn't mention isn't playable either (deleted between the two
    calls)."""
    playable: list[str] = []
    for item in body.get("items", []):
        video_id = item.get("id")
        status = item.get("status") or {}
        details = item.get("contentDetails") or {}
        rating = details.get("contentRating") or {}
        if not isinstance(video_id, str):
            continue
        if status.get("embeddable") is not True:
            continue
        if status.get("privacyStatus") == "private":
            continue
        if status.get("uploadStatus") not in (None, "processed"):
            continue
        if rating.get("ytRating") == "ytAgeRestricted":
            continue
        playable.append(video_id)
    return playable


def _get_json(url: str, what: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The URL carries the key; log the status, never the URL.
        logger.error("youtube %s failed: HTTP %s", what, exc.code)
        raise MediaSearchError("YouTube didn't answer just now.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.error("youtube %s failed: %s", what, type(exc).__name__)
        raise MediaSearchError("YouTube couldn't be reached just now.") from None
    if not isinstance(body, dict):
        logger.error("youtube %s returned a non-object body", what)
        raise MediaSearchError("YouTube didn't answer just now.")
    return body


def youtube_search(query: str, api_key: str | None = None) -> list[MediaResult]:
    """One `search.list` GET (100 quota units) for `SEARCH_CANDIDATES`
    videos, one `videos.list` GET (1 unit) to keep the embeddable ones,
    the first `MAX_RESULTS` of those. Blocking on purpose -- see the
    module docstring."""
    key = api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise MediaSearchError("YouTube isn't set up on this device yet.")
    params = {
        "part": "snippet",
        "type": "video",
        "maxResults": str(SEARCH_CANDIDATES),
        "videoEmbeddable": "true",
        "safeSearch": "moderate",
        "q": query,
        "key": key,
    }
    body = _get_json(f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}", "search")
    candidates = parse_search_response(body, limit=SEARCH_CANDIDATES)
    if not candidates:
        return []
    status_params = {
        "part": "status,contentDetails",
        "id": ",".join(c.video_id for c in candidates),
        "key": key,
    }
    try:
        status_body = _get_json(
            f"{_VIDEOS_URL}?{urllib.parse.urlencode(status_params)}", "videos.list"
        )
    except MediaSearchError:
        # The search worked; its own videoEmbeddable filter is what's
        # left. Said in the log so a later "that one won't play" can be
        # traced here rather than blamed on the player.
        logger.warning("embeddability check unavailable; offering unchecked search results")
        playable = [c.video_id for c in candidates]
    else:
        playable = playable_video_ids(status_body)
        dropped = [c.video_id for c in candidates if c.video_id not in playable]
        if dropped:
            logger.info("search dropped %d non-embeddable video(s): %s", len(dropped), dropped)
    kept = [c for c in candidates if c.video_id in playable][:MAX_RESULTS]
    return [
        MediaResult(index=i + 1, video_id=c.video_id, title=c.title) for i, c in enumerate(kept)
    ]


Broadcast = Callable[[dict[str, Any]], None]
Search = Callable[[str], list[MediaResult]]


class MediaController:
    """What the tool closes over. Holds the conversation's media memory
    (`last_results`, what last played), the volume and the layout, and
    emits one `media` message per action through `broadcast`.

    `set_broadcast()` exists because `cli.py` builds the tool before the
    screen server exists; the server installs the seam once it has a
    loop to dispatch on. Until then (and in unit tests) messages are
    dropped, not queued -- a message about a screen that isn't there
    yet has nothing to say to a screen that arrives later."""

    def __init__(
        self,
        search: Search | None = None,
        broadcast: Broadcast | None = None,
        cards: CardController | None = None,
    ) -> None:
        self._search: Search = search or youtube_search
        self._broadcast: Broadcast | None = broadcast
        # With a CardController, the offer is a Choice card (screen/
        # cards.py): the same three numbered titles, but tappable and
        # dismissable, and spoken from the card's own text. The panel's
        # own results view is then not drawn -- two copies of the same
        # three lines beside the face would be the "dense list" the
        # brief rules out. Without one (tests, a screen without cards)
        # the panel's results view is what she sees, as before.
        self._cards = cards
        self._card_id: str | None = None
        if cards is not None:
            cards.on_answer(self._on_card_answer)
        self.last_results: list[MediaResult] = []
        self.last_query: str | None = None
        self.now_playing: MediaResult | None = None
        self.playing = False
        self.paused = False
        self.volume = DEFAULT_VOLUME
        self.fullscreen = False
        # Videos the player reported it could not play (region-blocked,
        # embedding disabled despite the status check -- rare now, not
        # impossible). Without this, "carry on" after an error re-emits
        # the same unplayable video forever, each time telling the model
        # "it's starting now".
        self.unplayable: set[str] = set()

    def set_broadcast(self, broadcast: Broadcast | None) -> None:
        self._broadcast = broadcast

    # -- the choice card ---------------------------------------------------

    @property
    def card_id(self) -> str | None:
        """The id of the Choice card currently offering `last_results`,
        or None. Exposed for the server tests and for whoever needs to
        know a media card is up."""
        return self._card_id

    def _offer_card(self, title: str) -> Card:
        """Show `last_results` as a card (Choice for two or three, Confirm
        for one) and return it. The id is remembered *before* `show()`:
        a tap can land the moment the screen draws the card, and the
        dismiss `show()` reports for the card it replaces must not be
        mistaken for the new card's answer (TODO M9, found in review of
        the calling stream, same shape here)."""
        results = self.last_results
        if len(results) >= 2:
            card = choice(title, [r.title for r in results])
        else:
            # One result is a yes/no, not a choice of one (choice()
            # refuses it, rightly). Found in review: this raised.
            card = confirm(f"Play {results[0].title}?")
        self._card_id = card.id
        assert self._cards is not None
        self._cards.show(card)
        return card

    def _on_card_answer(self, answer: Answer) -> None:
        """A tap on the media Choice card (or its dismissal, from a tap
        or from a replacing `show()`). A voice answer also lands here,
        because the tool answers the card through the same door -- but
        the tool is already about to play, so only a tap starts
        playback from this callback. The tap is the user gesture the
        browser's autoplay rule wants, so playback starting here is
        "from the interaction", not from a timer."""
        if answer.card_id != self._card_id:
            return
        self._card_id = None
        if answer.source == "voice":
            return
        if answer.dismissed or answer.yes is False:
            return  # "never mind" / "no": the results stay referenceable by voice
        if answer.yes is True and self.last_results:
            self._play(self.last_results[0])  # the single-result Confirm card
            return
        n = answer.choice
        if n is not None and 1 <= n <= len(self.last_results):
            self._play(self.last_results[n - 1])

    def _settle_card(self, result: MediaResult | None) -> None:
        """Playback is starting (or everything is stopping): the offer
        card, if up, is answered -- with the choice she made, when the
        result is one of its options -- or cleared. The card's own
        callback sees `source="voice"` and leaves the playing to us."""
        card_id = self._card_id
        if card_id is None or self._cards is None:
            return
        self._card_id = None
        current = self._cards.current
        if current is None or current.id != card_id:
            return  # already replaced by someone else's card
        if result is not None and result in self.last_results:
            picked = {"yes": True} if current.kind == "confirm" else {"choice": result.index}
            if self._cards.answer(card_id, picked, source="voice"):
                return
        self._cards.answer(card_id, {"dismiss": True}, source="voice")

    # -- the browser reporting back ------------------------------------

    def on_browser_event(
        self, event: str, video_id: str | None = None, code: str | int | None = None
    ) -> None:
        """`ended`/`error`/`reset` from the browser. The browser reports,
        it doesn't decide: the controller is the one place media state
        lives, so "play that again" after a video ended still knows
        what "that" was. `reset` is a freshly loaded page saying it has
        no player -- whatever was playing isn't any more; the memory of
        what was offered and last played is kept, so "carry on" starts
        it again instead of insisting it's already on.

        An `error` is never silent: it is logged with the player's
        code, the video is never offered again, and if it was one of
        the results on offer, the rest are put back on the card so she
        sees the device noticed and can pick another -- by tap, or by
        number on the next turn."""
        if event == "error":
            self.playing = False
            self.paused = False
            failed = video_id or (self.now_playing.video_id if self.now_playing else None)
            logger.warning("player could not play %s (code %s)", failed, code)
            if failed:
                self.unplayable.add(failed)
                self._reoffer_without(failed)
        elif event in ("ended", "reset"):
            self.playing = False
            self.paused = False

    def _reoffer_without(self, failed: str) -> None:
        if self._cards is None or failed not in {r.video_id for r in self.last_results}:
            return
        remaining = [r for r in self.last_results if r.video_id not in self.unplayable]
        self.last_results = [
            MediaResult(index=i + 1, video_id=r.video_id, title=r.title)
            for i, r in enumerate(remaining)
        ]
        if self.now_playing is not None and self.now_playing.video_id == failed:
            self.now_playing = None
        if self.last_results:
            self._offer_card(RETRY_CARD_TITLE)
        else:
            self._settle_card(None)  # nothing left to offer: no card pretending otherwise

    # -- the handler -----------------------------------------------------

    def handle(
        self, action: str, query: str | None = None, choice: int | None = None
    ) -> dict[str, Any]:
        if action not in ACTIONS:
            return {"status": "error", "note": f"unknown action {action!r}"}
        method = getattr(self, f"_do_{action}")
        if action == "search":
            return method(query)
        if action == "play":
            return method(choice)
        return method()

    def _emit(self, action: str, **fields: Any) -> None:
        if self._broadcast is None:
            return
        self._broadcast({"type": "media", "action": action, **fields})

    def _do_search(self, query: str | None) -> dict[str, Any]:
        if not query or not query.strip():
            return {"status": "error", "note": "Ask her what she'd like to hear; no query given."}
        query = query.strip()
        try:
            results = self._search(query)
        except MediaSearchError as exc:
            return {
                "status": "unavailable",
                "note": f"{exc} Say so plainly and briefly; don't offer any titles.",
            }
        # A video the player already failed on is never offered again --
        # by voice or on a card -- so a tap can't land on one. Renumbered
        # so what she hears and taps is still One, Two, Three.
        if self.unplayable:
            playable = [r for r in results if r.video_id not in self.unplayable]
            results = [
                MediaResult(index=i + 1, video_id=r.video_id, title=r.title)
                for i, r in enumerate(playable)
            ]
        if not results:
            return {
                "status": "none",
                "query": query,
                "note": "Nothing was found for that. Say so briefly and ask what else she'd like.",
            }
        # A new search means she wants something else: whatever was
        # playing stops so the offer isn't read over it (see DECISIONS).
        self.last_results = results
        self.last_query = query
        self.playing = False
        self.paused = False
        if self._cards is not None and self.now_playing is not None:
            # With cards, the panel's results view isn't drawn, so the
            # results message can't be what takes the player down (as
            # it is in media-panel.js without cards): say stop outright.
            self._emit("stop")
        if self._cards is not None:
            card = self._offer_card(CARD_TITLE)
            return {
                "status": "ok",
                "query": query,
                "results": [r.spoken for r in results],
                "spoken": card.spoken,
                # The real model, asked to keep replies to two sentences,
                # offered one of three titles when first tried (probe,
                # 2026-09-25). She sees three; she must hear three.
                "note": (
                    "They are on screen as a numbered card she can tap. Say exactly this, "
                    "all of it, one short sentence per title even though replies are "
                    f"usually two sentences: \"{card.spoken}\" She can answer by tapping "
                    "or by saying a number. Nothing is playing yet."
                ),
            }
        self._emit("results", query=query, results=[r.as_message() for r in results])
        return {
            "status": "ok",
            "query": query,
            "results": [r.spoken for r in results],
            "note": (
                "These are now on screen, numbered. Read all of them out loud, in this "
                "order, each as its number then its title exactly as given -- one short "
                "sentence per title is right here even though replies are usually two "
                "sentences -- then ask which one she'd like. Don't add titles that "
                "aren't here. Nothing is playing yet."
            ),
        }

    def _resolve(self, choice: int | None) -> MediaResult | None:
        if choice is not None:
            for result in self.last_results:
                if result.index == choice:
                    return result
            return None
        # "That one" / "play it": the one most recently meant. If
        # something was picked before, that; else the only thing she
        # can mean is the first of what was just offered.
        if self.now_playing is not None and self.now_playing in self.last_results:
            return self.now_playing
        return self.last_results[0] if self.last_results else None

    def _play(self, result: MediaResult) -> dict[str, Any]:
        if result.video_id in self.unplayable:
            others = [r.spoken for r in self.last_results if r.video_id not in self.unplayable]
            return {
                "status": "unplayable",
                "results": others,
                "note": (
                    f"{result.spoken} can't be played on this screen. Say so briefly and "
                    + ("offer the others by number." if others else "offer to look for "
                       "something else.")
                ),
            }
        self._settle_card(result)
        self.now_playing = result
        self.playing = True
        self.paused = False
        self._emit(
            "play",
            video_id=result.video_id,
            title=result.title,
            index=result.index,
            volume=self.volume,
            fullscreen=self.fullscreen,
        )
        return {
            "status": "ok",
            "playing": result.spoken,
            # She picked; it plays; nothing needs saying. `say: ""` ends
            # the turn without a second model call (see the module
            # docstring); `did` is what the conversation remembers.
            "say": "",
            "did": f"Started playing {result.spoken} on the screen.",
            "note": "It's starting on the screen now. Nothing more is said this turn.",
        }

    def _do_play(self, choice: int | None) -> dict[str, Any]:
        if not self.last_results:
            return {
                "status": "nothing_to_play",
                "note": "Nothing has been searched for yet. Ask what she'd like to hear.",
            }
        result = self._resolve(choice)
        if result is None:
            offered = "; ".join(r.spoken for r in self.last_results)
            return {
                "status": "no_such_choice",
                "results": [r.spoken for r in self.last_results],
                "note": f"There are only {len(self.last_results)}: {offered}. Ask which.",
            }
        return self._play(result)

    def _do_again(self) -> dict[str, Any]:
        if self.now_playing is None:
            return {
                "status": "nothing_to_play",
                "note": "Nothing has played yet, so there's nothing to play again.",
            }
        return self._play(self.now_playing)

    def _do_next(self) -> dict[str, Any]:
        if not self.last_results:
            return {
                "status": "nothing_to_play",
                "note": "Nothing has been searched for yet. Ask what she'd like to hear.",
            }
        if self.now_playing is None or self.now_playing not in self.last_results:
            return self._play(self.last_results[0])
        following = self.now_playing.index + 1
        if following > len(self.last_results):
            return {
                "status": "end_of_results",
                "note": (
                    "That was the last of the three. Offer to look for something else "
                    "(a new search) rather than inventing a next one."
                ),
            }
        return self._play(self.last_results[following - 1])

    def _do_pause(self) -> dict[str, Any]:
        if not self.playing:
            return {"status": "nothing_playing", "note": "Nothing is playing to pause."}
        self.paused = True
        self._emit("pause")
        return {"status": "ok", "note": "Paused. One word is enough."}

    def _do_resume(self) -> dict[str, Any]:
        if self.now_playing is None:
            return {"status": "nothing_playing", "note": "Nothing has played yet to carry on."}
        if self.playing and not self.paused:
            return {"status": "ok", "note": "It's already playing. Say so in a few words."}
        if not self.playing:
            # It ended or was stopped: carrying on means starting it over.
            return self._play(self.now_playing)
        self.paused = False
        self._emit("resume")
        return {"status": "ok", "note": "Carrying on. One word is enough, then be quiet."}

    def _do_stop(self) -> dict[str, Any]:
        was_playing = self.playing
        self.playing = False
        self.paused = False
        self._settle_card(None)
        self._emit("stop")
        if not was_playing:
            return {"status": "ok", "note": "Nothing was playing; the screen is clear."}
        return {"status": "ok", "note": "Stopped. One word is enough."}

    def _do_never_mind(self) -> dict[str, Any]:
        """"Never mind" said out loud: the offer card goes, nothing
        starts, and the three results stay referenceable -- "actually,
        the second one" a moment later still works."""
        if self._card_id is None:
            return {"status": "ok", "note": "Nothing was being offered. Let it go; one word."}
        self._settle_card(None)
        return {"status": "ok", "note": "The choices are put away. One word is enough."}

    def _set_volume(self, level: int) -> dict[str, Any]:
        level = max(MIN_VOLUME, min(MAX_VOLUME, level))
        at_limit = level == self.volume
        self.volume = level
        self._emit("volume", level=level)
        if at_limit:
            edge = "as loud as it goes" if level == MAX_VOLUME else "as quiet as it goes"
            return {"status": "ok", "volume": level, "note": f"It's already {edge}. Say so."}
        return {"status": "ok", "volume": level, "note": "Done. One word is enough."}

    def _do_louder(self) -> dict[str, Any]:
        return self._set_volume(self.volume + VOLUME_STEP)

    def _do_quieter(self) -> dict[str, Any]:
        return self._set_volume(self.volume - VOLUME_STEP)

    def _do_bigger(self) -> dict[str, Any]:
        self.fullscreen = True
        self._emit("layout", mode="fullscreen")
        return {"status": "ok", "note": "It now fills the screen; your face is in the corner."}

    def _do_smaller(self) -> dict[str, Any]:
        self.fullscreen = False
        self._emit("layout", mode="panel")
        return {"status": "ok", "note": "It's back beside your face."}


def make_media_tool(controller: MediaController) -> Tool:
    """The `Tool`: same name and permission as the stub it replaces, so
    `Registry.call("play_music", granted)` gates it on `"music"` exactly
    as before. Only the schema grew (`action`, optional `query` and
    `choice`) and the handler is real."""

    def _play_music(
        action: Any = None, query: Any = None, choice: Any = None, **_ignored: Any
    ) -> dict[str, Any]:
        # The model's arguments, not a typed caller's: an unexpected key
        # or a wrong type must come back as a status dict the model can
        # recover from, never a TypeError that turns the whole turn into
        # "I didn't quite catch that" (server.py's fallback).
        if not isinstance(action, str):
            return {"status": "error", "note": "No action was given. Choose one and call again."}
        if query is not None and not isinstance(query, str):
            query = str(query)
        if isinstance(choice, bool):
            choice = None
        elif isinstance(choice, float) and choice.is_integer():
            choice = int(choice)
        elif isinstance(choice, str):
            choice = int(choice) if choice.strip().isdigit() else None  # "2" happens
        elif not isinstance(choice, int) and choice is not None:
            choice = None
        return controller.handle(action, query=query, choice=choice)

    return Tool(
        name="play_music",
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "query": {
                    "type": "string",
                    "description": "What to search for. Only with action 'search'.",
                },
                "choice": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_RESULTS,
                    "description": "Which of the offered results, 1 to 3. Only with 'play'.",
                },
            },
            "required": ["action"],
        },
        permission="music",
        handler=_play_music,
    )
