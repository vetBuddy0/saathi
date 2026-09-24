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
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from saathi.tools.registry import Tool

logger = logging.getLogger(__name__)

# Three, never more. Read aloud and shown; a fourth would be a list.
MAX_RESULTS = 3
# Titles on YouTube run long ("推荐50多岁以上的人真正喜欢的歌曲 ♣ 50首70、
# 80、90年代..." was the first real result). Trimmed for both screen and
# speech so a line stays a line and the model doesn't read a paragraph.
MAX_TITLE_CHARS = 60

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
)

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
    "the next result. 'pause', 'resume' ('carry on'), 'stop', 'louder', 'quieter', "
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


class MediaSearchError(RuntimeError):
    """The search could not be done -- no key, network, quota, bad
    response. Carries a plain-language reason the tool result can hand
    the model; the technical detail goes to the log."""


def clean_title(raw: str) -> str:
    """Unescape the API's HTML entities (`&amp;`, `&#39;`), collapse
    whitespace, and trim to `MAX_TITLE_CHARS` at a word boundary where
    one exists (CJK titles have none; a hard cut is fine there)."""
    text = html.unescape(raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_TITLE_CHARS:
        return text
    cut = text[:MAX_TITLE_CHARS]
    space = cut.rfind(" ")
    if space >= MAX_TITLE_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-|–—·♣♧") + "…"


def parse_search_response(body: dict[str, Any]) -> list[MediaResult]:
    """`search.list` -> at most three `MediaResult`s, numbered from one.
    Anything that isn't a video with an id and a title is skipped, not
    an error -- the API's own filtering (`type=video`,
    `videoEmbeddable=true`) is what's relied on for the rest."""
    results: list[MediaResult] = []
    for item in body.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        title = clean_title((item.get("snippet") or {}).get("title", ""))
        if not video_id or not title:
            continue
        results.append(MediaResult(index=len(results) + 1, video_id=video_id, title=title))
        if len(results) == MAX_RESULTS:
            break
    return results


def youtube_search(query: str, api_key: str | None = None) -> list[MediaResult]:
    """One `search.list` GET (100 quota units). Blocking on purpose --
    see the module docstring."""
    key = api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise MediaSearchError("YouTube isn't set up on this device yet.")
    params = {
        "part": "snippet",
        "type": "video",
        "maxResults": str(MAX_RESULTS),
        "videoEmbeddable": "true",
        "safeSearch": "moderate",
        "q": query,
        "key": key,
    }
    url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The URL carries the key; log the status, never the URL.
        logger.error("youtube search failed: HTTP %s", exc.code)
        raise MediaSearchError("YouTube didn't answer just now.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.error("youtube search failed: %s", type(exc).__name__)
        raise MediaSearchError("YouTube couldn't be reached just now.") from None
    if not isinstance(body, dict):
        logger.error("youtube search returned a non-object body")
        raise MediaSearchError("YouTube didn't answer just now.")
    return parse_search_response(body)


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

    def __init__(self, search: Search | None = None, broadcast: Broadcast | None = None) -> None:
        self._search: Search = search or youtube_search
        self._broadcast: Broadcast | None = broadcast
        self.last_results: list[MediaResult] = []
        self.last_query: str | None = None
        self.now_playing: MediaResult | None = None
        self.playing = False
        self.paused = False
        self.volume = DEFAULT_VOLUME
        self.fullscreen = False
        # Videos the player reported it could not play (region-blocked,
        # embedding disabled despite videoEmbeddable=true -- common).
        # Without this, "carry on" after an error re-emits the same
        # unplayable video forever, each time telling the model "it's
        # starting now".
        self.unplayable: set[str] = set()

    def set_broadcast(self, broadcast: Broadcast | None) -> None:
        self._broadcast = broadcast

    # -- the browser reporting back ------------------------------------

    def on_browser_event(self, event: str, video_id: str | None = None) -> None:
        """`ended`/`error`/`reset` from the browser. The browser reports,
        it doesn't decide: the controller is the one place media state
        lives, so "play that again" after a video ended still knows
        what "that" was. `reset` is a freshly loaded page saying it has
        no player -- whatever was playing isn't any more; the memory of
        what was offered and last played is kept, so "carry on" starts
        it again instead of insisting it's already on."""
        if event == "error":
            self.playing = False
            self.paused = False
            failed = video_id or (self.now_playing.video_id if self.now_playing else None)
            if failed:
                self.unplayable.add(failed)
        elif event in ("ended", "reset"):
            self.playing = False
            self.paused = False

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
        self._emit("results", query=query, results=[r.as_message() for r in results])
        return {
            "status": "ok",
            "query": query,
            "results": [r.spoken for r in results],
            # The real model, asked to keep replies to two sentences,
            # offered one of three titles when first tried (probe,
            # 2026-09-25). She sees three; she must hear three.
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
            "note": (
                "It's starting now on the screen. Say one short thing, naming it, and "
                "then stop talking so she can listen."
            ),
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
        self._emit("stop")
        if not was_playing:
            return {"status": "ok", "note": "Nothing was playing; the screen is clear."}
        return {"status": "ok", "note": "Stopped. One word is enough."}

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
