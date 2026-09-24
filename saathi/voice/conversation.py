"""Layers 1 and 2 of conversation memory: the verbatim window, and the
running summary of what has already fallen out of it.

**Why this exists.** Until this module, `cascade.py` rebuilt its
`messages` list from scratch on every turn — compiled context, a
language instruction, and the current transcript, nothing else. Every
turn was stateless, so no follow-up could land: "what about tomorrow?"
reached the model with no "tomorrow" to attach to. The tell was in the
`turns` table, not in anyone's judgement of the replies — `prompt_tokens`
sat at exactly 458 for all 31 turns logged across four days. A prompt
that never changes size is a prompt that never carries history.

**Why verbatim, and not summarised all the way down.** Pronouns and
deixis are the whole problem. "That one", "the other day", "her" and
"tomorrow" only resolve against the actual words that came before;
a summary that says *they discussed her appointment* has already
thrown away the referent that "it" needs. So the recent window is kept
word for word and only older material is compressed.

**Why a summary underneath, and not just a window.** A bare window
means everything older than six exchanges is simply gone — she would
forget the start of a long conversation while still in it, which is a
worse failure than never remembering, because it is invisible from
inside the conversation. When an exchange falls out of the window it is
folded into a short running summary that stays in the prompt, so the
window moving never makes something vanish outright.

**The option that lost:** letting the window grow unbounded and
trusting the model's context limit to be the only cap. Rejected on the
hot path — the prompt is re-sent in full every turn, so an unbounded
window buys coherence at a linearly growing latency and per-turn cost,
which is exactly the budget SPEC.md says to protect. A cap that is
explicit and small is worse at hour three of a conversation and much
better at every turn before it.

This module holds no model client and makes no calls. It decides *what*
should be remembered and in what shape; `cascade.py` decides when to
spend a model call folding it (between turns, never during one), and
`identity/digest.py` is what actually makes that call.
"""

from __future__ import annotations

from dataclasses import dataclass

# Six exchanges, or roughly a thousand tokens, whichever binds first.
# Six because it is about as far back as a pronoun realistically
# reaches in speech; the token cap because six exchanges of an unusually
# long-winded turn would otherwise blow the prompt budget the count
# alone can't see.
MAX_EXCHANGES = 6
MAX_TOKENS = 1000


def estimate_tokens(text: str) -> int:
    """Four characters to a token, the usual English rule of thumb.

    Deliberately an estimate and not a real tokenizer: every tokenizer
    worth the name is another dependency, and CLAUDE.md rules out
    adding one for this. The number only ever decides *when to evict*,
    where being off by a fifth costs one exchange of window either way
    — and the real, exact count from the API is what gets logged to
    `turns.prompt_tokens` per turn, so nothing downstream is relying on
    this approximation to be true.
    """
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class Exchange:
    """One complete round trip: what she said, and what Saathi said back."""

    user: str
    assistant: str

    def tokens(self) -> int:
        return estimate_tokens(self.user) + estimate_tokens(self.assistant)


class ConversationMemory:
    """The window (layer 1) and the summary (layer 2) for one open
    conversation. Not persisted — a restart is a new conversation, and
    what should survive one is an `episodes` row (layer 3), not this.
    """

    def __init__(self, max_exchanges: int = MAX_EXCHANGES, max_tokens: int = MAX_TOKENS) -> None:
        self._max_exchanges = max_exchanges
        self._max_tokens = max_tokens
        self._window: list[Exchange] = []
        self._summary = ""
        # Evicted but not yet folded into the summary. Folding costs a
        # model call, which only happens between turns, so an exchange
        # can sit here for the length of one turn. It is never dropped
        # from here without being folded first.
        self._unfolded: list[Exchange] = []

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def window(self) -> list[Exchange]:
        return list(self._window)

    def record(self, user: str, assistant: str) -> None:
        """Add a finished exchange, evicting whatever no longer fits.

        Evicted exchanges go to `unfolded()`, not to the floor — see
        this module's docstring on why a bare window is the wrong
        shape.
        """
        self._window.append(Exchange(user=user, assistant=assistant))
        while self._over_budget():
            self._unfolded.append(self._window.pop(0))

    def _over_budget(self) -> bool:
        if len(self._window) > self._max_exchanges:
            return True
        # Never evict the only exchange left, however long it is: a
        # single enormous turn should cost what it costs rather than
        # leave the model with no verbatim context at all.
        if len(self._window) <= 1:
            return False
        return sum(e.tokens() for e in self._window) > self._max_tokens

    def unfolded(self) -> list[Exchange]:
        """Exchanges evicted from the window and still missing from the
        summary."""
        return list(self._unfolded)

    def fold(self, summary: str) -> None:
        """Replace the summary with a freshly regenerated one and
        consider everything currently evicted accounted for.

        Regenerated wholesale rather than appended to: an appended
        summary grows without bound and drifts into a transcript of
        itself, which is the thing layer 1 is already for.
        """
        self._summary = summary.strip()
        self._unfolded = []

    def messages(self) -> list[dict[str, str]]:
        """The window as chat messages, oldest first."""
        messages: list[dict[str, str]] = []
        for exchange in self._window:
            messages.append({"role": "user", "content": exchange.user})
            messages.append({"role": "assistant", "content": exchange.assistant})
        return messages
