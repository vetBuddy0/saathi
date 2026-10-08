"""The test resident, Lee Kim Tan: a complete companion profile on this
device, so the care-home flow can be tried end to end on one person.

Why it exists: the medication seed (medication/seed.py) gives a ward of
schedules, but a companion also needs to *know* the person it talks to.
This writes what admission staff would tell it -- who he is, how he
likes to be spoken to, his family, his days -- as `rules`, each tracing
to one admission `episodes` row (SPEC.md: every rule records where it
came from), and saves his son as a contact with the relation "son", so
"call my son" finds him. Invented person, test data.

What is deliberately *not* here: his medicines, doses or conditions.
Those live in the medication store and never reach the model; the one
rule about medicine says only that the nurses give them and the nurse
explains.

Idempotent: a second run finds the admission episode and writes
nothing. Lost: retiring and rewriting the rules on every run -- each run
would leave a trail of retired duplicates in his profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from saathi.call.contacts import find_by_relation, save_contact
from saathi.identity.store import IdentityStore

NAME = "Lee Kim Tan"
BED = "6"
SON_NAME = "Udhi"  # the paired family member already saved with this number
SON_PHONE = "+6589614304"
SON_COUNTRY = "SG"

ADMISSION = (
    f"Admission profile for {NAME}, entered by care staff (test data). "
    "78, bed 6. Retired taxi driver of 35 years. Most at ease in Hokkien and "
    "Singlish. Son Udhi visits on Sunday mornings. Loves Teresa Teng and old "
    "Hokkien opera, plays Chinese chess most afternoons, kopi-o kosong in the "
    "morning, a garden walk with his stick after breakfast, a nap after lunch. "
    "A little hard of hearing on the left. Dislikes being fussed over about his "
    "health. Nurses give all his medicines."
)

RULES = (
    "His name is Lee Kim Tan, and he likes to be called Uncle Lee.",
    "He is 78 and lives in bed 6 at the care home.",
    "He is most at ease in Hokkien and Singlish.",
    "His son Udhi visits on Sunday mornings, and he looks forward to it all week.",
    "He drove a taxi for 35 years and still knows every road in Singapore; "
    "asking him the way somewhere makes him light up.",
    "He loves Teresa Teng's songs and old Hokkien opera.",
    "He plays Chinese chess most afternoons and is proud of being hard to beat.",
    "He likes his kopi-o kosong in the morning.",
    "He walks in the garden after breakfast with his walking stick, and naps after lunch.",
    "He is a little hard of hearing in his left ear, so keep sentences short and clear.",
    "He doesn't like being fussed over about his health; chat about other things first.",
    "The nurses give him all his medicines; if he asks about one, say the nurse will "
    "explain, and never talk about doses or changing them.",
)


@dataclass(frozen=True)
class Applied:
    episode_id: int
    rules_written: int
    son_saved: bool


def apply_test_profile(path: Path, *, now: datetime | None = None) -> Applied:
    now = now or datetime.now(timezone.utc)
    with IdentityStore(path) as store:
        store.create()
        existing = [e for e in store.read("episodes") if e["text"] == ADMISSION]
        if existing:
            episode_id, rules_written = existing[0]["id"], 0
        else:
            episode_id = store.append(
                "episodes", ts=now.isoformat(), entity_id=None, text=ADMISSION,
                importance=0.9, embedding=None,
            )
            for text in RULES:
                store.append("rules", text=text, confidence=1.0, learned_at=now.isoformat(),
                             source_episode=episode_id, active=1)
            rules_written = len(RULES)
    son = find_by_relation(path, "son")
    son_saved = False
    if son is None or son.phone != SON_PHONE:
        save_contact(path, SON_NAME, SON_PHONE, SON_COUNTRY, "son")
        son_saved = True
    return Applied(episode_id=episode_id, rules_written=rules_written, son_saved=son_saved)
