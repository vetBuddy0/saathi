"""The family-visible, family-editable view over what Saathi has learned.

Checkpoint 3, once `reflect.py` exists to produce rules worth showing.
Reads `rules`/`episodes` through `identity/store.py` only — never
touches SQLite directly, same discipline `compile.py` and `reflect.py`
already follow.

**Listing and describing rules** fold in each rule's own provenance —
the episode text it was reflected from, in plain language, not a bare
`source_episode` id a family member has no way to make sense of on
their own (SPEC.md: "a companion that learns something wrong ... with
no way to correct it is a support call nobody can answer" — being able
to *see* why Saathi believes something is half of that answer).

**Retracting a rule is the other half, and it is real since
2026-09-25.** `retract_rule()` calls `IdentityStore.retire("rules", id,
at)`: the row's `active` flips to 0, `compile.py` stops sending it, and
`list_rules(include_inactive=True)` still shows it, so what was once
believed stays reviewable. Before that date this function raised
`RetractionNotSupported`, because `IdentityStore` was `create`/`append`/
`read` only and there was no way to flip one row's flag under that
interface; the `preferences` fix (append-only, latest row wins) didn't
transfer, since retracting rule #47 means *that row*, not "the latest
rule about this topic". The option that lost, and why, is recorded in
`store.py`'s docstring (append-only retraction-event tables with
readers computing effective state). The spoken path is
`identity/correction.py`; this module is the programmatic one a family
review screen will call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from saathi.identity.store import IdentityStore


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


def retract_rule(store: IdentityStore, rule_id: int, *, at: datetime | None = None) -> None:
    """Stop Saathi believing rule `rule_id`. The row stays (inactive), so
    the family can still see what was believed and why. Raises
    `store.UnknownRow` for an id that matches nothing — a review screen
    acting on a stale id must find out, not think it succeeded."""
    store.retire("rules", rule_id, at or datetime.now(timezone.utc))
