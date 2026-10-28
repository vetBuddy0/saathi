"""call/routing.py + tools/calling.py -- paired people ring through the
free family app; Twilio only for people who aren't paired, and only
when it is configured. Through the real tool and registry permission
check, with Twilio and push faked at their seams."""

from __future__ import annotations

import pytest

from saathi.call import contacts
from saathi.call.choosing import ChoiceFlow
from saathi.call.controller import CallController
from saathi.call.family import FamilyRegistry
from saathi.call.hangup import FakeHoldSeam
from saathi.call.push import Subscription
from saathi.call.relay import FakeRelay
from saathi.call.routing import FamilyRoute
from saathi.call.twilio import FakeTwilioClient, TwilioCredentials
from saathi.call.webrtc import FamilyCalls
from saathi.screen.cards import CardController
from saathi.tools.calling import make_call_tool, make_contact_dialer
from saathi.tools.registry import Registry

CREDS = TwilioCredentials(
    "AC" + "0" * 32, "SK" + "1" * 32, "s", "+1" + "0" * 10, "+1" + "0" * 9 + "1"
)
SUB = Subscription("https://push.test/endpoint", b"\x04" + b"\x01" * 64, b"\x02" * 16)


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


class FakePush:
    public_key = "K"

    def __init__(self):
        self.sent = []

    def send(self, subscription, payload, **_):
        self.sent.append(payload)


@pytest.fixture
def setup(tmp_path):
    store = tmp_path / "identity.sqlite3"
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    client = FakeTwilioClient()
    client.call_resource = {"status": "ringing"}
    hold = FakeHoldSeam()
    controller = CallController(
        CREDS, client, FakeRelay(), _Server(), _Audio, hold, poll_interval_seconds=60
    )
    push = FakePush()
    calls = FamilyCalls(
        registry,
        push=push,
        public_url=lambda: "https://saathi.example.test",
        hold=hold,
        other_active=lambda: controller.active,
    )
    calls._ensure_ticker = lambda: None
    ready = {"reason": None}
    route = FamilyRoute(registry, calls, lambda: ready["reason"])
    cards = CardController()
    choices = ChoiceFlow(cards, make_contact_dialer(controller, route))
    tool = make_call_tool(controller, store, choices, family=route)
    reg = Registry()
    reg.register(tool)

    def call(contact):
        return reg.call("call_contact", frozenset({"calls"}), contact=contact)

    def pair(name, relation=None):
        member, _ = registry.redeem(registry.issue_pairing_token()[0], name, relation, "Mum")
        registry.set_subscription(member.id, SUB)
        return member

    yield {
        "store": store, "client": client, "push": push, "calls": calls, "call": call,
        "pair": pair, "ready": ready, "controller": controller, "cards": cards,
        "registry": registry,
    }
    controller.hangup(wait=True)
    calls.hangup()


def test_a_paired_person_with_a_saved_number_rings_through_the_app_not_twilio(setup):
    contacts.save_contact(setup["store"], "Priya", "+6591234567", "SG", "daughter")
    setup["pair"]("Priya", "daughter")
    for said in ("Priya", "my daughter"):
        reply = setup["call"](said)
        assert reply["status"] == "calling", said
        assert setup["client"].created == []  # Twilio never dialled
        assert setup["push"].sent[-1]["kind"] == "ring"
        setup["calls"].hangup()


def test_someone_not_paired_falls_back_to_twilio_when_it_is_configured(setup):
    contacts.save_contact(setup["store"], "Vasudev", "+6598765432", "SG", "son")
    setup["pair"]("Priya", "daughter")
    reply = setup["call"]("Vasudev")
    assert reply["status"] == "calling"
    assert setup["client"].created[0][0] == "+6598765432"
    assert setup["push"].sent == []


def test_a_paired_person_with_no_number_is_found_by_name_and_relation(setup):
    setup["pair"]("Anand", "grandson")
    assert setup["call"]("my grandson")["status"] == "calling"
    setup["calls"].hangup()
    assert setup["call"]("Anand")["status"] == "calling"


def test_someone_in_both_lists_is_one_candidate_not_a_which_one_card(setup):
    contacts.save_contact(setup["store"], "Priya", "+6591234567", "SG", None)
    setup["pair"]("Priya")
    assert setup["call"]("Priya")["status"] == "calling"
    assert setup["cards"].current is None


def test_without_twilio_an_unpaired_number_is_an_honest_no(tmp_path):
    store = tmp_path / "identity.sqlite3"
    contacts.save_contact(store, "Vasudev", "+6598765432", "SG", "son")
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    calls = FamilyCalls(registry, push=FakePush(), public_url=lambda: None, hold=FakeHoldSeam())
    route = FamilyRoute(registry, calls)
    tool = make_call_tool(None, store, None, family=route)
    reply = tool.handler(contact="my son")
    assert reply["status"] == "unavailable" and "haven't paired" in reply["note"]
    assert tool.handler(contact="the test number")["status"] == "unavailable"


def test_while_the_family_app_is_starting_a_paired_person_is_told_so(setup):
    setup["pair"]("Priya", "daughter")
    setup["ready"]["reason"] = "The family app is still starting up."
    reply = setup["call"]("Priya")
    assert reply["status"] == "unavailable" and "starting up" in reply["note"]
    assert setup["push"].sent == [] and setup["client"].created == []


def test_one_call_at_a_time_across_both_paths(setup):
    contacts.save_contact(setup["store"], "Vasudev", "+6598765432", "SG", "son")
    setup["pair"]("Priya", "daughter")
    assert setup["call"]("Priya")["status"] == "calling"
    assert setup["call"]("Vasudev")["status"] == "busy"
    setup["calls"].hangup()
    assert setup["call"]("Vasudev")["status"] == "calling"
    assert setup["call"]("Priya")["status"] == "busy"
