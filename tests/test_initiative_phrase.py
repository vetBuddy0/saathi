"""initiative/phrase.py -- the constraint on the one model call that
happens after policy has already decided. Pinned with the exact
fabricated utterances the dry run actually produced: each must be
caught, for the specific reason it's wrong.
"""

from types import SimpleNamespace

from saathi.initiative.phrase import check, phrase, template

# The real reason + source the dry run gave the model for each of these.
PRIYA_SOURCE = (
    "Social connection with her daughter Priya serves as a significant source of "
    "comfort and engagement for her. "
    "She mentioned her daughter Priya is planning to visit this Saturday."
)
SCAN_SOURCE = (
    "She is experiencing anxiety related to the upcoming scan, indicated by nervousness "
    "about the appointment and difficulties with sleep. "
    "She mentioned she has a scan on Thursday morning and sounded a little nervous about it."
)
SLEEP_SOURCE = (
    "Poor sleep quality is correlated with increased knee pain and reduced physical "
    "activity. She said she's been having trouble sleeping this week."
)
TABLET_SOURCE = "Reminder due: take your morning blood pressure tablet [reminder_id=1]"


# -- the exact dry-run fabrications ---------------------------------------


def test_priya_is_planning_to_call_this_evening_is_caught():
    utterance = (
        "It looks like Priya is planning to call this evening. I hope that gives you "
        "something lovely to look forward to while I keep you company."
    )
    kinds = {v.kind for v in check(utterance, PRIYA_SOURCE)}
    texts = {v.text.lower() for v in check(utterance, PRIYA_SOURCE)}
    # "Priya" IS in this source, so it's allowed -- what's wrong here is
    # the invented call and its timing.
    assert "named_entity" not in kinds
    assert "is planning" in texts
    assert "this evening" in texts


def test_priya_is_a_named_entity_violation_when_the_source_never_mentions_her():
    utterance = "It looks like Priya is planning to call this evening."
    violations = check(utterance, SLEEP_SOURCE)
    assert any(v.kind == "named_entity" and v.text == "Priya" for v in violations)


def test_ive_made_a_special_arrangement_is_caught():
    utterance = (
        "I've made a special arrangement to ensure tomorrow's scan goes as smoothly and "
        "calmly as possible, so please take a deep breath and trust we've got you covered."
    )
    texts = {v.text.lower() for v in check(utterance, SCAN_SOURCE)}
    assert "i've made" in texts
    assert "arrangement" in texts
    assert "tomorrow" in texts
    assert "we've got you covered" in texts


def test_i_did_that_is_caught():
    utterance = (
        "I noticed we talked about fixing your sleep, and I did that because getting good "
        "rest really helps ease knee aches."
    )
    texts = {v.text.lower() for v in check(utterance, SLEEP_SOURCE)}
    assert "i did" in texts


# -- what must pass --------------------------------------------------------


def test_a_faithful_reminder_utterance_passes():
    utterance = (
        "Good morning! Just a gentle nudge to remember your blood pressure tablet before "
        "you start your day."
    )
    assert check(utterance, TABLET_SOURCE) == []


def test_recalling_and_asking_passes():
    utterance = "You mentioned the scan on Thursday. How are you feeling about it?"
    assert check(utterance, SCAN_SOURCE) == []


def test_a_day_name_from_the_source_is_allowed():
    assert check("Thursday is the scan, you said.", SCAN_SOURCE) == []


def test_a_day_name_not_in_the_source_is_a_named_entity():
    violations = check("Your scan is on Friday.", SCAN_SOURCE)
    assert any(v.kind == "named_entity" and v.text == "Friday" for v in violations)


def test_will_and_ll_are_claims():
    assert any(v.kind == "claim" for v in check("It will be fine.", SCAN_SOURCE))
    assert any(v.kind == "claim" for v in check("You'll feel better.", SCAN_SOURCE))


def test_shall_i_is_an_offer_not_a_claim():
    assert check("Shall I sit with you a while?", SCAN_SOURCE) == []


def test_sentence_initial_capital_is_not_a_named_entity():
    assert check("Just checking in. Hope the knee is easier.", SLEEP_SOURCE) == []


# -- phrase(): retry, then template ---------------------------------------


class _ScriptedClient:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        reply = self._replies.pop(0) if self._replies else ""
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def test_phrase_returns_a_passing_first_attempt():
    client = _ScriptedClient(["You mentioned the scan on Thursday. How are you feeling?"])
    out = phrase(client, "noticed", SCAN_SOURCE, "")
    assert out.startswith("You mentioned")
    assert client.calls == 1


def test_phrase_retries_once_then_falls_back_to_the_template():
    client = _ScriptedClient(
        [
            "I've made a special arrangement for tomorrow's scan.",
            "Priya will call tonight.",
        ]
    )
    out = phrase(client, "noticed", "the reason", "She mentioned the scan.")
    assert client.calls == 2
    assert out == template("noticed", "the reason", "She mentioned the scan.")
    assert "She mentioned the scan." in out


def test_template_for_a_reminder_strips_the_id_tag():
    assert template("scheduled", TABLET_SOURCE, "") == (
        "A reminder: take your morning blood pressure tablet."
    )


def test_phrase_only_sends_the_reason_and_source_to_the_model():
    client = _ScriptedClient(["You mentioned the scan. How are you?"])
    phrase(client, "noticed", SCAN_SOURCE, "the note")
    # Nothing else -- no persona file, no memory, no store -- reaches the
    # prompt. If it did, it'd be a place for a "fact" to come from.
    assert client.calls == 1
