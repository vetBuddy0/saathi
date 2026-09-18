"""The family-visible, family-editable view over what Saathi has learned.

Checkpoint 3, once `reflect.py` exists to produce rules worth showing.
Reads `rules`/`episodes` through `identity/store.py` only — never
touches SQLite directly, same discipline `compile.py` and `reflect.py`
already follow.

**Listing and describing rules are real and working.** `list_rules()`
and `describe_rule()` fold in each rule's own provenance — the episode
text it was reflected from, in plain language, not a bare
`source_episode` id a family member has no way to make sense of on
their own (SPEC.md: "a companion that learns something wrong ... with
no way to correct it is a support call nobody can answer" — this module
is half of that answer; being able to *see* why Saathi believes
something is the half this pass builds).

**Retracting a rule is not built, on purpose, not silently skipped.**
`IdentityStore` is `create`/`append`/`read` only — no `update`, no
`delete` — and `rules.active` is a mutable per-row flag with no way to
flip it under that interface. This is the same shape of problem
`identity/preferences.py` hit with `preferences.key`, except that fix
(making the table append-only, "latest row wins") doesn't transfer
cleanly here: a `preferences` reader only ever cares about the *current*
value for a key, but a family member retracting rule #47 needs to
retract *that specific row*, not "the latest rule about this topic" —
there's no natural key to make rules append-only the same way. A real
fix needs `IdentityStore` to gain some form of update capability (even
a narrowly-scoped one, e.g. flipping a named boolean column by row id —
not a general-purpose "update anything"), which is a bigger, cross-
cutting interface question than this module should decide on its own:
`reminders.active` and `initiatives.spoken` have the exact same shape of
problem, just not yet hit by real code that needs to flip them. Proposed
as a SPEC.md diff, not applied — see this commit's discussion.
`retract_rule()` below is real code, not a stub: it raises
`RetractionNotSupported` with this exact explanation, so a caller finds
out clearly and immediately rather than the call silently doing nothing
or corrupting a row.
"""

from __future__ import annotations

from typing import Any

from saathi.identity.store import IdentityStore


class RetractionNotSupported(NotImplementedError):
    """Raised by `retract_rule()` — see this module's docstring for why
    this is a real, structural gap, not a temporarily-unwired feature."""

    def __init__(self, rule_id: int) -> None:
        super().__init__(
            f"can't retract rule {rule_id}: IdentityStore has no update capability, "
            "only create/append/read. See saathi/identity/profile.py's docstring for "
            "the proposed SPEC.md fix."
        )
        self.rule_id = rule_id


def _episode_summary(store: IdentityStore, episode_id: int | None) -> str | None:
    if episode_id is None:
        return None
    rows = store.read("episodes", id=episode_id)
    if not rows:
        return None
    return rows[0]["text"]


def list_rules(store: IdentityStore, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Every rule Saathi currently holds, each with `source_text` folded
    in — the episode it was reflected from, in plain language, or `None`
    if that episode no longer exists or the rule predates provenance
    tracking. Inactive rules are excluded by default: `active` exists so
    a retracted rule stops being sent to the model (`compile.py` already
    filters on it), and a family review screen's default view should
    match what she's actually hearing, not the full history underneath
    it."""
    rules = store.read("rules")
    if not include_inactive:
        rules = [r for r in rules if r.get("active", 1)]
    result = []
    for rule in rules:
        result.append({**rule, "source_text": _episode_summary(store, rule.get("source_episode"))})
    return result


def describe_rule(store: IdentityStore, rule_id: int) -> dict[str, Any] | None:
    """One rule, with provenance, or `None` if it doesn't exist —
    ordinary and expected (a stale id from a review screen that's since
    been refreshed), not an error."""
    rows = store.read("rules", id=rule_id)
    if not rows:
        return None
    rule = rows[0]
    return {**rule, "source_text": _episode_summary(store, rule.get("source_episode"))}


def retract_rule(store: IdentityStore, rule_id: int) -> None:
    """Always raises `RetractionNotSupported` today — see this module's
    docstring. Exists now, rather than being added later, so callers
    (a future family-facing UI) can be written against the real shape of
    this function ahead of the schema fix landing."""
    raise RetractionNotSupported(rule_id)
