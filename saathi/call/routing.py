"""Which way a call goes: the free family app, or the phone network.

Exists so `call_contact` can stay one tool with one permission while
there are two ways to ring someone. The order is the product owner's
(DECISIONS 2026-10-07): a person paired with the family app is always
rung through it -- it costs nothing per minute -- and Twilio is used
only when it is configured *and* the person isn't paired. Twilio is
kept, not deleted: a landline, or a relative who never installs the
app, still has a phone number.

The family registry and her saved contacts are two lists joined by
name (see `call/family.py` for why they are two). For matching, a
paired member who has no saved number is offered as a contact with an
empty phone; `dial()` then finds the pairing by name. Someone who is in
both lists is one candidate, not two -- otherwise every paired daughter
would produce a "which Priya?" card.

Nothing here executes on its own: `tools/calling.py`'s handler, reached
only through the registry's permission check, calls `dial()`.
"""

from __future__ import annotations

from typing import Any, Callable

from saathi.call.contacts import Contact, normalise_name
from saathi.call.family import FamilyRegistry
from saathi.call.webrtc import FamilyCalls

# Synthetic ids for paired-only members, below any real entity id, so a
# Contact built here can never be mistaken for a row in her memory.
_FAMILY_ID_BASE = -1_000_000


class FamilyRoute:
    def __init__(
        self,
        registry: FamilyRegistry,
        calls: FamilyCalls,
        ready: Callable[[], str | None] = lambda: None,
    ) -> None:
        """`ready()` returns None when the family app can ring someone,
        else the reason it can't yet (no public URL yet)."""
        self._registry = registry
        self._calls = calls
        self._ready = ready

    @property
    def active(self) -> bool:
        return self._calls.active

    def contacts(self, saved: list[Contact]) -> list[Contact]:
        """`saved` plus paired members not already among them by name.
        A saved contact who is also paired gains the pairing's relation."""
        members = self._registry.members()
        by_name = {normalise_name(m.name): m for m in members}
        merged: list[Contact] = []
        seen: set[str] = set()
        for contact in saved:
            key = normalise_name(contact.name)
            seen.add(key)
            member = by_name.get(key)
            if member is not None and member.relation and member.relation not in contact.relations:
                contact = Contact(
                    contact.id,
                    contact.name,
                    contact.phone,
                    contact.country,
                    contact.relations + (member.relation,),
                )
            merged.append(contact)
        for index, member in enumerate(members):
            key = normalise_name(member.name)
            if key in seen:
                continue
            seen.add(key)
            relations = (member.relation,) if member.relation else ()
            merged.append(Contact(_FAMILY_ID_BASE - index, member.name, "", None, relations))
        return merged

    def dial(self, name: str) -> dict[str, Any] | None:
        """A tool result if `name` is paired (rung through the app, or
        an honest "not yet"); None if not paired -- the caller then
        falls back to the phone network."""
        member = self._registry.find_by_name(name)
        if member is None:
            return None
        reason = self._ready()
        if reason is not None:
            return {
                "status": "unavailable",
                "note": f"{reason} Say so plainly in one short sentence.",
            }
        return self._calls.dial(member)


def no_phone_route(name: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "note": (
            f"{name} isn't set up for calls from this device: they haven't paired the "
            "family app, and phone calls aren't configured here. Say so plainly in one "
            "short sentence."
        ),
    }
