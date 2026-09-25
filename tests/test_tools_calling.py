"""saathi/tools/calling.py — the real call_contact: same name/permission
as the stub, results carry no number or SID, dial runs from a worker
thread as it does in production."""

import concurrent.futures
import re

from saathi.call.controller import CallController
from saathi.call.hangup import FakeHoldSeam
from saathi.call.relay import FakeRelay
from saathi.call.twilio import FakeTwilioClient, TwilioCredentials
from saathi.tools.calling import CALL_CONTACT_DESCRIPTION, make_call_tool
from saathi.tools.llm_schema import tool_to_openai_schema
from saathi.tools.registry import PermissionDenied, Registry

CREDS = TwilioCredentials(
    "AC" + "0" * 32, "SK" + "1" * 32, "s", "+1" + "0" * 10, "+1" + "0" * 9 + "1"
)
_SECRET_SHAPES = re.compile(r"AC[0-9a-f]{32}|SK[0-9a-f]{32}|\+?[0-9]{8,}|https?://")


class _Server:
    def start(self):
        pass

    def stop(self):
        pass


class _Audio:
    def start(self, send):
        pass

    def feed_inbound(self, ulaw):
        pass

    def stop(self):
        pass


def _controller(client=None):
    client = client or FakeTwilioClient()
    return CallController(CREDS, client, FakeRelay(), _Server(), _Audio, FakeHoldSeam()), client


def test_registers_under_the_stubs_name_and_permission_and_has_a_schema():
    controller, _ = _controller()
    tool = make_call_tool(controller)
    assert tool.name == "call_contact" and tool.permission == "calls"
    schema = tool_to_openai_schema(tool, CALL_CONTACT_DESCRIPTION)
    assert schema["function"]["name"] == "call_contact"
    assert "contact" in schema["function"]["parameters"]["properties"]


def test_goes_through_the_registrys_permission_check():
    controller, client = _controller()
    registry = Registry()
    registry.register(make_call_tool(controller))
    try:
        registry.call("call_contact", frozenset(), contact="the test number")
        raise AssertionError("permission check did not run")
    except PermissionDenied:
        pass
    assert client.created == []
    result = registry.call("call_contact", frozenset({"calls"}), contact="the test number")
    assert result["status"] == "calling"
    assert len(client.created) == 1


def test_the_test_number_dials_and_the_note_says_calling_without_the_number():
    controller, client = _controller()
    result = make_call_tool(controller).handler(contact="Call the TEST number please")
    assert result["status"] == "calling"
    assert "Calling the test number." in result["note"]
    assert not _SECRET_SHAPES.search(repr(result))
    assert client.created[0][0] == CREDS.test_number


def test_any_other_contact_is_a_polite_not_yet_and_dials_nothing():
    controller, client = _controller()
    result = make_call_tool(controller).handler(contact="my daughter")
    assert result["status"] == "unavailable"
    assert client.created == []
    assert not controller.active


def test_a_dial_failure_is_reported_sanitized_not_raised():
    leaky = RuntimeError("https://api.twilio.com/x " + "+1" + "0" * 9 + "1")
    client = FakeTwilioClient(fail_with=leaky)
    controller, _ = _controller(client)
    result = make_call_tool(controller).handler(contact="test")
    assert result["status"] == "error"
    assert not _SECRET_SHAPES.search(repr(result))


def test_a_call_already_in_progress_is_reported_busy():
    controller, client = _controller()
    tool = make_call_tool(controller)
    tool.handler(contact="test")
    result = tool.handler(contact="test")
    assert result["status"] == "busy" and len(client.created) == 1


def test_handler_works_from_a_worker_thread():
    controller, client = _controller()
    tool = make_call_tool(controller)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(tool.handler, contact="the test number").result(timeout=2.0)
    assert result["status"] == "calling" and len(client.created) == 1


# -- Stage 2 -----------------------------------------------------------------

from saathi.call import contacts as _contacts  # noqa: E402
from saathi.call.cards import FakeCardController  # noqa: E402
from saathi.call.saving import SaveFlow  # noqa: E402
from saathi.identity.store import IdentityStore  # noqa: E402
from saathi.tools.calling import (  # noqa: E402
    ANSWER_CARD_DESCRIPTION,
    SAVE_CONTACT_DESCRIPTION,
    make_answer_card_tool,
    make_save_contact_tool,
)


def _store(tmp_path):
    path = tmp_path / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
    return path


def test_call_my_daughter_dials_her_saved_number_and_says_her_name(tmp_path):
    path = _store(tmp_path)
    _contacts.save_contact(path, "Priya", "+65" + "9123" + "4567", "SG", "daughter")
    controller, client = _controller()
    result = make_call_tool(controller, path).handler(contact="my daughter")
    assert result["status"] == "calling"
    assert "Calling Priya." in result["note"]
    assert client.created[0][0] == "+65" + "9123" + "4567"
    assert not _SECRET_SHAPES.search(repr(result))


def test_an_exact_name_dials(tmp_path):
    path = _store(tmp_path)
    _contacts.save_contact(path, "Ravi", "+65" + "9876" + "5432", "SG", None)
    controller, client = _controller()
    assert make_call_tool(controller, path).handler(contact="ravi")["status"] == "calling"


def test_no_match_never_dials_and_offers_to_save(tmp_path):
    path = _store(tmp_path)
    controller, client = _controller()
    result = make_call_tool(controller, path).handler(contact="my sister")
    assert result["status"] == "no_match" and "offer to save" in result["note"]
    assert client.created == []


def test_voice_save_flow_through_the_tools_from_worker_threads(tmp_path):
    path = _store(tmp_path)
    cards = FakeCardController()
    flow = SaveFlow(path, cards, lambda: "SG")
    save = make_save_contact_tool(flow)
    answer = make_answer_card_tool(cards, [flow])
    assert save.permission == "contacts" and answer.permission == "calls"
    assert tool_to_openai_schema(save, SAVE_CONTACT_DESCRIPTION)["function"]["name"]
    assert tool_to_openai_schema(answer, ANSWER_CARD_DESCRIPTION)["function"]["name"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(save.handler, name="Priya", relation="daughter",
                            number="nine one two three, four five six seven").result(5)
        assert first["status"] == "entry" and "plus six five" in first["note"]
        second = pool.submit(answer.handler, yes=True).result(5)
    assert second["status"] == "saved"
    assert _contacts.find_by_relation(path, "daughter").name == "Priya"


def test_a_spoken_number_or_name_edits_the_entry_card_and_yes_saves_the_edit(tmp_path):
    path = _store(tmp_path)
    cards = FakeCardController()
    flow = SaveFlow(path, cards, lambda: "SG")
    save = make_save_contact_tool(flow)
    answer = make_answer_card_tool(cards, [flow])
    save.handler(name="Priya", relation="daughter", number="nine one two three four five six seven")
    card = cards.current
    changed = answer.handler(number="nine one two three, four five six eight")
    assert changed["status"] == "changed" and "four five six eight" in changed["note"]
    # Shown as she said it (no country code); resolved again on the yes.
    assert cards.current.id == card.id and cards.current.number == "9123 4568"
    assert answer.handler(name="Anita")["status"] == "changed"
    assert cards.current.name == "Anita"
    assert answer.handler(number="um, er")["status"] == "not_understood"
    assert answer.handler(yes=True)["status"] == "saved"
    saved = _contacts.find_by_relation(path, "daughter")
    assert saved.name == "Anita" and saved.phone == "+65" + "9123" + "4568"


def test_answer_card_with_nothing_pending(tmp_path):
    cards = FakeCardController()
    flow = SaveFlow(_store(tmp_path), cards, lambda: "SG")
    assert make_answer_card_tool(cards, [flow]).handler(yes=True)["status"] == "no_card"


def test_answer_card_passes_the_first_one_as_choice_1(tmp_path):
    from saathi.call.cards import choice as _choice

    cards = FakeCardController()
    card = _choice("Who?", ["Basudeb", "Vasudev"])

    class _Flow:
        def pending_card_ids(self):
            return {card.id}

        def outcome(self, card_id):
            return None

    seen = []
    cards.on_answer(seen.append)
    cards.show(card)
    result = make_answer_card_tool(cards, [_Flow()]).handler(choice=1)
    assert result["status"] == "answered" and seen[0].choice == 1


# -- Stage 3 -----------------------------------------------------------------

from saathi.call.choosing import ChoiceFlow  # noqa: E402
from saathi.tools.calling import make_contact_dialer  # noqa: E402


def _stage3(tmp_path, *people):
    path = _store(tmp_path)
    for name, number in people:
        _contacts.save_contact(path, name, number, "SG", None)
    controller, client = _controller()
    cards = FakeCardController()
    choices = ChoiceFlow(cards, make_contact_dialer(controller))
    call = make_call_tool(controller, path, choices)
    answer = make_answer_card_tool(cards, [choices])
    return call, answer, cards, client


A = "+65" + "9123" + "4567"
B = "+65" + "9876" + "5432"
C = "+65" + "9555" + "0000"


def test_confident_says_the_saved_name_and_dials(tmp_path):
    call, _, cards, client = _stage3(tmp_path, ("Basudeb", A), ("Priya", B))
    result = call.handler(contact="Vasudev")
    assert result["status"] == "calling" and "Calling Basudeb." in result["note"]
    assert client.created[0][0] == A and cards.current is None


def test_unsure_shows_a_choice_and_dials_nothing_until_she_answers(tmp_path):
    call, answer, cards, client = _stage3(tmp_path, ("Meena", A), ("Mina", B), ("Ravi", C))
    result = call.handler(contact="Meena")
    assert result["status"] == "unsure" and client.created == []
    card = cards.current
    assert card.kind == "choice" and {o.label for o in card.options} == {"Meena", "Mina"}
    assert card.spoken in result["note"] and "the first" in card.spoken
    chosen = card.options[0].label  # the real cards carry Option objects; the name is the label
    picked = answer.handler(choice=1)  # "the first one"
    assert picked["status"] == "calling" and f"Calling {chosen}." in picked["note"]
    assert client.created[0][0] == (A if chosen == "Meena" else B)


def test_a_rejected_choice_dials_nothing(tmp_path):
    call, answer, cards, client = _stage3(tmp_path, ("Meena", A), ("Mina", B))
    call.handler(contact="Meena")
    assert answer.handler(choice=3)["status"] == "no_card"  # out of range: refused
    assert client.created == [] and cards.current is not None


def test_a_single_unsure_name_is_confirmed_by_name_before_dialling(tmp_path):
    call, answer, cards, client = _stage3(tmp_path, ("Anand", A))
    result = call.handler(contact="Anant")
    assert result["status"] == "unsure" and cards.current.kind == "confirm"
    assert "Did you mean Anand?" in result["note"] and client.created == []
    assert answer.handler(yes=False)["status"] == "not_calling"
    assert client.created == []


def test_no_match_with_saved_contacts_asks_from_them_and_never_dials(tmp_path):
    # Changed 2026-09-25 at the user's request ("make it ask from contacts
    # if it's really unsure"): nothing matched "Suresh", but Priya is saved,
    # so she's asked "Did you mean Priya?" instead of told there's no number.
    call, _, cards, client = _stage3(tmp_path, ("Priya", A))
    result = call.handler(contact="Suresh")
    assert result["status"] == "unsure" and "Did you mean Priya?" in result["note"]
    assert client.created == [] and cards.current.kind == "confirm"


def test_no_match_and_nothing_saved_says_so_and_never_dials(tmp_path):
    call, _, cards, client = _stage3(tmp_path)
    result = call.handler(contact="Suresh")
    assert result["status"] == "no_match" and "offer to save" in result["note"]
    assert client.created == [] and cards.current is None


def test_the_model_saying_your_son_still_finds_her_son(tmp_path):
    from saathi.call import contacts

    assert contacts.normalise_relation("your son") == contacts.normalise_relation("my son")


def test_a_tap_on_the_choice_dials_off_the_calling_thread(tmp_path):
    import time

    call, _, cards, client = _stage3(tmp_path, ("Meena", A), ("Mina", B))
    call.handler(contact="Mina")
    card = cards.current
    assert cards.answer(card.id, {"choice": 2}, source="tap")
    for _ in range(100):
        if client.created:
            break
        time.sleep(0.01)
    assert client.created[0][0] == (A if card.options[1] == "Meena" else B)


def test_unsure_without_a_choice_flow_is_treated_as_no_match(tmp_path):
    path = _store(tmp_path)
    _contacts.save_contact(path, "Meena", A, "SG", None)
    _contacts.save_contact(path, "Mina", B, "SG", None)
    controller, client = _controller()
    result = make_call_tool(controller, path).handler(contact="Meena")
    assert result["status"] == "no_match" and client.created == []
