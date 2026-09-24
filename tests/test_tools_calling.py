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
