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
        assert first["status"] == "readback" and "plus six five" in first["note"]
        second = pool.submit(answer.handler, yes=True).result(5)
    assert second["status"] == "saved"
    assert _contacts.find_by_relation(path, "daughter").name == "Priya"


def test_answer_card_with_nothing_pending(tmp_path):
    cards = FakeCardController()
    flow = SaveFlow(_store(tmp_path), cards, lambda: "SG")
    assert make_answer_card_tool(cards, [flow]).handler(yes=True)["status"] == "no_card"
