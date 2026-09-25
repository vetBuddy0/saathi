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


@dataclass(frozen=True)
class Context:
    """What the router is allowed to know about the conversation.
    `results_offered`: a music search has put titles on offer this
    session. `results_count`: how many (0 when none). `card_pending`:
    a calling card (choice or read-back) is waiting for her answer."""

    results_offered: bool = False
    results_count: int = 0
    card_pending: bool = False


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
    "please", "saathi", "hey saathi", "hey", "can you", "could you", "would you",
    "will you", "i want to", "i'd like to", "i would like to", "i want you to",
    "let's", "lets", "just", "now", "and", "then", "so",
)
_SUFFIXES = ("please", "now", "for me", "thank you", "thanks", "dear", "saathi")
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
              "hold on a moment", "pause the music", "pause the video", "pause the song"),
    "resume": ("carry on", "continue", "resume", "keep going", "play", "go on", "unpause",
               "play it", "carry on playing", "continue playing"),
    "stop": ("stop", "stop it", "stop that", "close it", "close youtube", "turn it off",
             "stop the music", "stop the video", "stop the song", "stop playing",
             "close the video", "turn that off", "switch it off"),
    "louder": ("louder", "volume up", "turn it up", "turn up the volume", "turn the volume up",
               "a bit louder", "make it louder", "more volume", "too quiet", "it's too quiet"),
    "quieter": ("quieter", "volume down", "turn it down", "turn down the volume",
                "turn the volume down", "softer", "a bit quieter", "make it quieter",
                "make it softer", "less volume", "too loud", "it's too loud", "lower"),
    "bigger": ("bigger", "full screen", "make it bigger", "make it big", "fullscreen",
               "make it full screen", "larger", "make it larger"),
    "smaller": ("smaller", "make it smaller", "make it small", "back beside you",
                "not full screen", "shrink it"),
    "next": ("next", "next one", "another one", "a different one", "skip", "skip it",
             "skip this", "skip this one", "the next one", "play the next one",
             "something different", "a different video", "another"),
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
    """The one entry point. Pure: same words and context, same answer."""
    context = context or Context()
    clause = _last_clause(heard)
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

    if context.results_offered:
        action = _MEDIA_PHRASES.get(clause)
        if action is not None:
            return Command(
                tool="play_music", arguments={"action": action}, speak=_MEDIA_SPEAK[action]
            )
    return None
