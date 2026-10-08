"""A deterministic command router: clear commands become a tool call
before the model is asked anything.

Why it exists: live, "pause", "volume up", "smaller" and "call my son"
sometimes came back as a spoken sentence ("I've turned up the volume
for you") with no tool call at all -- the model *described* the action
instead of asking for it (2026-09-26, `cloud/demo`, gpt-4.1 with six
tools offered). A command that fails one time in ten is a device she
stops trusting. A rule cannot fail to fire on the input it matches, so
the clear commands are matched here, on the transcript, and handed to
the same intent callback the model's tool calls go through. Everything
fuzzy ("something cheerful", "what was that song") still goes to the
model unchanged.

Contested -- the option that lost: a second, smaller model deciding
which tool to call (TypeSafe's JEV, or a two-model split). It would
still be probabilistic on exactly the inputs that are failing, it adds
a network hop to every turn, and JEV needs an OpenRouter key this
project doesn't have. It stays a next step only if *fuzzy* tool choice
turns out to be the remaining failure (TODO.md).

What the router knows: strings, and two facts about the conversation
carried in `Context` -- whether music results are on offer, and whether
a calling card is waiting for an answer. It never imports a tool, never
reads a controller, never executes anything: it returns a `Command`
(tool name, arguments, what to say) and `cascade.py` emits it as an
intent, exactly as it would a model's tool call. "The voice engine
never executes anything. It emits intent; the core validates; the tool
executes" holds unchanged.

Media commands only match once music has been offered this session:
"louder" with nothing playing means her own voice, "stop" with nothing
playing means "stop talking", and both belong to the model. Bare
numbers ("two") only match while results are on offer; "the second
one" likewise. "Yes" / "no" alone only match while a calling card is
up. "Hang up" is not routed: calling exposes no tool for it (the two-
second spacebar hold is the hang-up), so the model answers as before.

While music is playing (`Context.media_playing`, 2026-10-08) the
media phrases match even if no search was offered this session (a song
started some other way is still a song), and a media command right
after her name is taken wherever the name falls -- mid-song the
transcript can begin with a line of the lyrics before "Saathi, stop".
Only the exact media phrases get that treatment; any other words after
the name go through the ordinary path. Lost: matching "stop" anywhere in
a mid-song transcript -- "don't stop" and the lyrics themselves would
stop the song.

Multi-clause utterances ("pause... no, actually play the second one"):
the last clause is what she means, and it alone is matched. If the
last clause isn't a command, nothing is routed and the model sees the
whole sentence -- a wrong match costs more than a slow one.

Fixed replies ("Paused.") apply only when the tool answers `status:
"ok"`; any other status (nothing playing, no such number) goes to the
model with the tool's note, so a routed "pause" with nothing playing
is still answered honestly. Volume is always phrased by the model from
the note: the tool reports "already as loud as it goes" under the same
status, and "Louder." would be a lie there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from saathi.audio.wake import _is_name, strip_wake_word


@dataclass(frozen=True)
class Context:
    """What the router is allowed to know about the conversation.
    `results_offered`: a music search has put titles on offer this
    session. `results_count`: how many (0 when none). `card_pending`:
    a calling card (choice or read-back) is waiting for her answer.
    `media_playing`: something is playing (or paused) right now."""

    results_offered: bool = False
    results_count: int = 0
    card_pending: bool = False
    # Music is playing (or paused) on the screen right now.
    media_playing: bool = False


@dataclass(frozen=True)
class Command:
    """`speak=None`: the tool's note is phrased by the model in a follow-
    up completion, as after any tool call. A string (possibly empty):
    say exactly this when the tool answers ok, with no model call; ""
    means the action speaks for itself and nothing is said."""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    speak: str | None = None


_PREFIXES = (
    "no", "yes", "ok", "okay", "oh", "um", "uh", "hmm", "well", "actually", "wait",
    "please", "kaki", "hey kaki", "hey", "can you", "could you", "would you",
    "will you", "i want to", "i'd like to", "i would like to", "i want you to",
    "let's", "lets", "just", "now", "and", "then", "so",
)
_SUFFIXES = ("please", "now", "for me", "thank you", "thanks", "dear", "kaki")
_PREFIX_RE = re.compile(r"^(?:%s)\b\s*" % "|".join(re.escape(p) for p in _PREFIXES))
_SUFFIX_RE = re.compile(r"\s*\b(?:%s)$" % "|".join(re.escape(s) for s in _SUFFIXES))
_CLAUSE_SPLIT = re.compile(r"[.,;:!?…—\-]+|\bno\b\s+(?=actually|wait)")
_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)

_YES = {"yes", "yeah", "yep", "yes please", "that's right", "thats right", "right", "correct"}
_NO = {"no", "nope", "no thanks", "no thank you", "wrong", "that's wrong", "thats wrong"}

# Media commands, exact after normalisation. Fixed reply per action.
_MEDIA_SPEAK: dict[str, str | None] = {
    "pause": "Paused.",
    "resume": "Carrying on.",
    "stop": "Stopped.",
    "louder": None,
    "quieter": None,
    "bigger": "",
    "smaller": "",
    "next": None,
    "again": None,
    "never_mind": "",
}
_MEDIA_PHRASES: dict[str, str] = {}
for _action, _phrases in {
    "pause": ("pause", "pause it", "pause that", "hold on", "wait a moment", "wait a minute",
              "hold on a moment", "pause the music", "pause the video", "pause the song",
              "pause music", "pause song"),
    "resume": ("carry on", "continue", "resume", "keep going", "play", "go on", "unpause",
               "play it", "carry on playing", "continue playing", "resume the music",
               "resume the song", "resume playing", "resume music", "play the music again"),
    "stop": ("stop", "stop it", "stop that", "close it", "close youtube", "turn it off",
             "stop the music", "stop the video", "stop the song", "stop playing",
             "close the video", "turn that off", "switch it off", "stop music",
             "stop song", "stop the youtube"),
    "louder": ("louder", "volume up", "turn it up", "turn up the volume", "turn the volume up",
               "a bit louder", "make it louder", "more volume", "too quiet", "it's too quiet",
               "increase the volume", "increase volume", "raise the volume", "volume louder",
               "little louder", "a little louder"),
    "quieter": ("quieter", "volume down", "turn it down", "turn down the volume",
                "turn the volume down", "softer", "a bit quieter", "make it quieter",
                "make it softer", "less volume", "too loud", "it's too loud", "lower",
                "decrease the volume", "decrease volume", "reduce the volume", "reduce volume",
                "lower the volume", "lower volume", "a bit softer", "a little softer",
                "little softer", "a little quieter"),
    "bigger": ("bigger", "full screen", "make it bigger", "make it big", "fullscreen",
               "make it full screen", "larger", "make it larger"),
    "smaller": ("smaller", "make it smaller", "make it small", "back beside you",
                "not full screen", "shrink it"),
    "next": ("next", "next one", "another one", "a different one", "skip", "skip it",
             "skip this", "skip this one", "the next one", "play the next one",
             "something different", "a different video", "another", "next song",
             "play next", "play the next song", "next video", "skip the song",
             "skip song"),
    "again": ("again", "play it again", "that one again", "play that again", "once more",
              "play again", "one more time", "repeat", "repeat it", "play that one again"),
    "never_mind": ("never mind", "nevermind", "forget it", "leave it", "leave it be",
                   "never mind then", "forget about it", "no thanks", "not now"),
}.items():
    for _p in _phrases:
        _MEDIA_PHRASES[_p] = _action

_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "1": 1,
    "second": 2, "2nd": 2, "two": 2, "2": 2,
    "third": 3, "3rd": 3, "three": 3, "3": 3,
}
_CHOICE_RE = re.compile(
    r"^(?:play |i'll have |i'll take |i want |let's have |give me |put on )?"
    r"(?:the |number |option |choice )?"
    r"(?P<n>first|second|third|1st|2nd|3rd|one|two|three|1|2|3)"
    r"(?: one| video| song)?$"
)

_CALL_RE = re.compile(r"^(?:call|ring|phone|telephone|dial)\s+(?P<who>.+)$")
# Objects that make "call ..." an idiom or a plan, not a request to dial now.
_CALL_BLOCKED = {
    "it", "me", "this", "that", "him", "her", "them", "a", "an", "back", "later", "tomorrow",
    "tonight", "again", "off", "out", "up", "in", "on", "you", "yourself", "someone", "somebody",
    "anyone", "who", "whom", "what", "when", "for", "to", "about", "if", "and", "or", "soon",
}
_CALL_MAX_WORDS = 3


def normalise(text: str) -> str:
    text = text.lower().strip()
    text = _PUNCT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _last_clause(heard: str) -> str:
    """The last clause that says anything: a trailing ", please" or
    ", thank you" is politeness, not what she means."""
    lowered = heard.lower().replace("’", "'")
    parts = [normalise(p) for p in _CLAUSE_SPLIT.split(lowered) if p and p.strip()]
    for clause in reversed(parts):
        if clause and (clause in _YES or clause in _NO or _strip_filler(clause)):
            return clause
    return ""


def _strip_filler(clause: str) -> str:
    previous = None
    while previous != clause:
        previous = clause
        clause = _PREFIX_RE.sub("", clause).strip()
        clause = _SUFFIX_RE.sub("", clause).strip()
    return clause


def _call(clause: str) -> Command | None:
    match = _CALL_RE.match(clause)
    if match is None:
        return None
    who = match.group("who").strip()
    words = who.split()
    if not words or len(words) > _CALL_MAX_WORDS:
        return None
    if any(word in _CALL_BLOCKED for word in words):
        return None
    if words[0] in ("the", "a", "an") and who != "the test number":
        return None
    return Command(tool="call_contact", arguments={"contact": who}, speak=None)


def route(heard: str, *, context: Context | None = None) -> Command | None:
    """The one entry point. Pure: same words and context, same answer.
    Her name at the start is not part of the command: a wake-word turn
    carries "Saathi" in its audio, and the cloud transcriber spells it
    every way ("Sothai, call Udhi") -- so it is matched the way the
    wake word is (audio/wake.py), not only as the literal filler."""
    context = context or Context()
    command = _media_command_after_name(heard, context)
    if command is not None:
        return command
    clause = _last_clause(strip_wake_word(heard.strip()))
    if not clause:
        return None

    # A card's yes/no first, before "yes"/"no" are stripped as filler.
    if context.card_pending:
        if clause in _YES:
            return Command(tool="answer_card", arguments={"yes": True}, speak=None)
        if clause in _NO:
            return Command(tool="answer_card", arguments={"yes": False}, speak=None)
    elif context.results_offered and context.results_count == 1 and clause in _YES:
        return Command(tool="play_music", arguments={"action": "play", "choice": 1}, speak=None)

    clause = _strip_filler(clause)
    if not clause:
        return None

    command = _call(clause)
    if command is not None:
        return command

    choice = _CHOICE_RE.match(clause)
    if choice is not None:
        n = _ORDINALS[choice.group("n")]
        if context.card_pending:
            return Command(tool="answer_card", arguments={"choice": n}, speak=None)
        if context.results_offered:
            return Command(
                tool="play_music", arguments={"action": "play", "choice": n}, speak=None
            )
        return None

    if context.results_offered or context.media_playing:
        action = _MEDIA_PHRASES.get(clause)
        if action is not None:
            return Command(
                tool="play_music", arguments={"action": action}, speak=_MEDIA_SPEAK[action]
            )
    return None


def _after_last_name(heard: str) -> str | None:
    """What she said after the *last* time her name appears, or None
    when it doesn't. Mid-song the transcript can open with a line of the
    lyrics the mic caught before she spoke ("...in love with your body,
    Saathi, stop"); her name marks where her own words start."""
    words = list(re.finditer(r"[^\W_]+", heard, flags=re.UNICODE))
    for i in range(len(words) - 1, -1, -1):
        word = words[i].group().lower()
        joined = word + words[i + 1].group().lower() if i + 1 < len(words) else ""
        if _is_name(word):
            return heard[words[i].end():]
        if joined and _is_name(joined):
            return heard[words[i + 1].end():]
    return None


def _media_command_after_name(heard: str, context: Context) -> Command | None:
    """While music plays: a media command right after her name, wherever
    the name falls in the transcript. Only media commands, and only the
    exact phrases -- anything else still goes to `route()` as a whole."""
    if not context.media_playing:
        return None
    tail = _after_last_name(heard)
    if not tail:
        return None
    clause = _strip_filler(_last_clause(tail))
    action = _MEDIA_PHRASES.get(clause)
    if action is None:
        return None
    return Command(tool="play_music", arguments={"action": action}, speak=_MEDIA_SPEAK[action])


# What the on-device transcriber (tiny.en) makes of "call" in an Indian
# English voice, measured on en-IN clips (2026-10-07). Used only by
# sounds_complete(), which decides *when* a hands-free turn stops -- the
# words themselves are still transcribed by the cloud STT afterwards.
_LOCAL_CALL_SPELLINGS = re.compile(r"^(?:kaul|kol|col|cal|kall|caul|cole|coal)\b")
# A command can't end on these: "call my..." is a pause, not a request.
_DANGLING = {
    "my", "our", "your", "his", "her", "their", "the", "a", "an", "to", "and", "or",
    "some", "of", "for", "with", "mr", "mrs", "miss", "doctor", "dr", "uncle", "auntie",
}


def sounds_complete(text: str, *, media_playing: bool = False) -> bool:
    """Whether words heard so far (on-device, rough) already make a
    whole command the router would act on, so a hands-free turn can end
    at a short pause instead of waiting for the long one. Conservative
    by construction: anything the router wouldn't route -- including
    every question and anything ending mid-phrase -- is "not yet".
    `media_playing`: music is on, so "stop" and "louder" are whole
    commands too (without it they'd wait for the long pause, since the
    router only takes them once music is in play)."""
    clause = _last_clause(strip_wake_word(text.strip()))
    if not clause:
        return False
    if clause.split()[-1] in _DANGLING:
        return False
    clause = _LOCAL_CALL_SPELLINGS.sub("call", _strip_filler(clause))
    return route(clause, context=Context(media_playing=media_playing)) is not None
