"""Contacts are people in her memory, not rows in an address book.

Exists to be the first writer of `entities` and `edges` (SPEC.md,
"Memory") and to define the convention every later reader follows.
There is deliberately no `contacts` table: a daughter she mentions in
conversation and a daughter she can phone are the same person, and a
parallel table would split them — "call my daughter" works because the
graph already knows who her daughter is.

The convention (recorded in DECISIONS.md 2026-09-25):
- She is one `entities` row, `kind="self"`, `name="self"` — the `src` of
  every relationship edge. Created on first need; the lowest id wins if
  two ever exist.
- A person is `kind="person"`, `name` as she says it. The phone lives in
  `notes` as JSON: `{"phone": "+65...", "country": "SG"}` — `notes` is
  the only free field the schema has. Nothing that builds the model's
  context (`compile.py`, `digest.py`) reads `entities` at all, so a
  number in `notes` never reaches the model.
- A relationship is `edges(src=self, dst=person, relation="daughter",
  since=<iso>, until=None)`.

Correcting a wrong number: `IdentityStore` is append-only on `main` —
no update, no delete, and `edges` has no id to target. A new number is
therefore a *new* person row with the same name (and a new edge if a
relation was given), and every read here is **latest wins**: the
highest entity id per normalised name, the most recent edge per
relation. The superseded rows stay as history. When a narrow
`retire()` lands (TODO.md), superseded edges can get their `until`
set; nothing here depends on it.

Threading: tool handlers run on the screen server's executor thread and
card taps on its loop thread, never the thread that opened the store.
Every function takes a *path* and opens its own short-lived connection
(the `identity/preferences.py:threadsafe_reader` pattern).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from saathi.identity.store import IdentityStore

SELF_KIND = "self"
PERSON_KIND = "person"

# Spoken form -> canonical relation. Small on purpose: the relations an
# older person living alone actually phones. Unknown words pass through
# lowercased, so "my physio" still becomes an edge called "physio".
_RELATION_ALIASES = {
    "daughter": "daughter", "son": "son",
    "wife": "wife", "husband": "husband",
    "sister": "sister", "brother": "brother",
    "mother": "mother", "mum": "mother", "mom": "mother", "mummy": "mother", "ma": "mother",
    "father": "father", "dad": "father", "daddy": "father", "papa": "father",
    "granddaughter": "granddaughter", "grandson": "grandson",
    "niece": "niece", "nephew": "nephew",
    "daughter in law": "daughter-in-law", "son in law": "son-in-law",
    "friend": "friend", "neighbour": "neighbour", "neighbor": "neighbour",
    "doctor": "doctor", "nurse": "nurse", "carer": "carer", "caregiver": "carer",
}
_POSSESSIVE_RE = re.compile(r"^(?:my|our)\s+|'s$|\s+number$")


def normalise_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(folded.lower().split())


def normalise_relation(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = normalise_name(text).replace("-", " ")
    previous = None
    while previous != cleaned:
        previous, cleaned = cleaned, _POSSESSIVE_RE.sub("", cleaned).strip()
    if not cleaned:
        return None
    return _RELATION_ALIASES.get(cleaned, cleaned)


def is_known_relation(text: str | None) -> bool:
    relation = normalise_relation(text)
    return relation is not None and relation in _RELATION_ALIASES.values()


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    phone: str
    country: str | None
    relations: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_self(store: IdentityStore) -> int:
    rows = store.read("entities", kind=SELF_KIND)
    if rows:
        return min(row["id"] for row in rows)
    return store.append("entities", kind=SELF_KIND, name="self", notes=None, created_at=_now())


def _phone_of(row: dict) -> tuple[str | None, str | None]:
    try:
        notes = json.loads(row.get("notes") or "{}")
    except json.JSONDecodeError:
        return None, None
    if not isinstance(notes, dict):
        return None, None
    return notes.get("phone"), notes.get("country")


def save_contact(
    path: Path, name: str, phone_e164: str, country: str | None, relation: str | None
) -> int:
    """Appends the person (and the edge). Returns the new entity id."""
    with IdentityStore(path) as store:
        store.create()
        self_id = _ensure_self(store)
        notes = json.dumps({"phone": phone_e164, "country": country})
        person_id = store.append(
            "entities", kind=PERSON_KIND, name=name.strip(), notes=notes, created_at=_now()
        )
        canonical = normalise_relation(relation)
        if canonical:
            store.append(
                "edges", src=self_id, dst=person_id, relation=canonical, since=_now(), until=None
            )
        return person_id


def list_contacts(path: Path) -> list[Contact]:
    """Every person with a phone, latest row per normalised name."""
    with IdentityStore(path) as store:
        store.create()
        people = store.read("entities", kind=PERSON_KIND)
        self_rows = store.read("entities", kind=SELF_KIND)
        edges = store.read("edges", src=min(r["id"] for r in self_rows)) if self_rows else []
    latest: dict[str, dict] = {}
    for row in people:
        key = normalise_name(row["name"])
        if key not in latest or row["id"] > latest[key]["id"]:
            latest[key] = row
    by_id = {row["id"]: row for row in people}
    relations: dict[str, list[str]] = {}
    for relation, dst in _latest_edges(edges).items():
        if dst in by_id:
            relations.setdefault(normalise_name(by_id[dst]["name"]), []).append(relation)
    contacts = []
    for key, row in latest.items():
        phone, country = _phone_of(row)
        if phone:
            contacts.append(
                Contact(row["id"], row["name"], phone, country, tuple(relations.get(key, ())))
            )
    return sorted(contacts, key=lambda c: c.id)


def _latest_edges(edges: list[dict]) -> dict[str, int]:
    """relation -> dst of its most recent edge (open ones only). Ties on
    `since` break by read order, which is insertion order."""
    latest: dict[str, tuple[str, int, int]] = {}
    for order, edge in enumerate(edges):
        if edge.get("until"):
            continue
        key = (edge.get("since") or "", order)
        current = latest.get(edge["relation"])
        if current is None or key > current[:2]:
            latest[edge["relation"]] = (key[0], key[1], edge["dst"])
    return {relation: value[2] for relation, value in latest.items()}


def find_by_relation(path: Path, relation: str) -> Contact | None:
    canonical = normalise_relation(relation)
    if canonical is None:
        return None
    return next((c for c in list_contacts(path) if canonical in c.relations), None)


def find_by_exact_name(path: Path, name: str) -> Contact | None:
    key = normalise_name(name)
    return next((c for c in list_contacts(path) if normalise_name(c.name) == key), None)
