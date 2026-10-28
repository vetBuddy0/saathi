"""saathi/screen/static/js/twilio-phone.js in headless Chromium, with a
fake Twilio.Device and a fake token endpoint.

What's pinned: no token, no status and no SDK; with a token the page
says it can call via Twilio; a dial waits for core.py's state to go free
and then connects with `To` = the number, once; the call's events go back
as `phone_event`s with the engine's id; the engine's hangup disconnects
the call; SIM frames (no `via`) are ignored.
"""

import pytest

from tests.chromium_harness import module_url, run_module_script

_SCENARIO = """
import { createTwilioPhone } from "%(module)s";
const out = {};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// The token endpoint: 404 first (an engine with no Twilio route), then a token.
let tokenOk = false;
window.fetch = async () => tokenOk
  ? { ok: true, json: async () => ({ token: "tok" }) }
  : { ok: false, json: async () => ({}) };

const connects = [];
class FakeCall {
  constructor() { this.handlers = {}; this.disconnected = 0; }
  on(name, fn) { this.handlers[name] = fn; }
  emit(name, arg) { this.handlers[name] && this.handlers[name](arg); }
  disconnect() { this.disconnected += 1; this.emit("disconnect"); }
}
class FakeDevice {
  constructor(token) { this.token = token; FakeDevice.made += 1; }
  on() {}
  async connect(options) { const c = new FakeCall(); connects.push({ options, c }); return c; }
}
FakeDevice.made = 0;
window.Twilio = { Device: FakeDevice };

const sent = [];
const phone = createTwilioPhone((m) => sent.push(m));

await phone.onConnected();
out.no_token = { sent: sent.slice(), made: FakeDevice.made };

tokenOk = true;
await phone.onConnected();
out.status = sent.slice();
out.made = FakeDevice.made;

// A SIM frame is not ours.
phone.onMessage({ type: "phone", action: "dial", number: "+6511111111" });
// Her turn is still on: the dial waits.
phone.onMessage({ type: "state", state: "thinking" });
phone.onMessage({ type: "phone", via: "twilio", action: "dial", id: "c1", number: "+6589614304" });
phone.onMessage({ type: "state", state: "speaking" });
await wait(600);
out.while_speaking = connects.length;
phone.onMessage({ type: "state", state: "idle" });
await wait(700);
out.connects = connects.map((x) => x.options.params.To);

sent.length = 0;
const call = connects[0].c;
call.emit("ringing");
call.emit("accept");
phone.onMessage({ type: "phone", via: "twilio", action: "hangup", id: "other" });
out.after_stale_hangup = call.disconnected;
phone.onMessage({ type: "phone", via: "twilio", action: "hangup", id: "c1" });
out.disconnected = call.disconnected;
out.events = sent.map((m) => [m.type, m.id, m.event]);

// A failed connect is reported, not swallowed.
FakeDevice.prototype.connect = async () => { throw new Error("mic denied"); };
sent.length = 0;
phone.onMessage({ type: "phone", via: "twilio", action: "dial", id: "c2", number: "+6589614304" });
await wait(700);
out.failed = sent.map((m) => [m.id, m.event, m.detail]);

document.body.dataset.out = JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def results() -> dict:
    return run_module_script(_SCENARIO % {"module": module_url("twilio-phone.js")})


def test_no_token_means_no_status_and_no_device(results):
    assert results["no_token"] == {"sent": [], "made": 0}


def test_with_a_token_the_page_says_it_can_call_via_twilio(results):
    assert results["status"] == [{"type": "phone_status", "via": "twilio", "can_call": True}]
    assert results["made"] == 1


def test_a_dial_waits_for_her_sentence_then_connects_once_to_the_number(results):
    assert results["while_speaking"] == 0
    assert results["connects"] == ["+6589614304"]


def test_the_calls_events_go_back_with_the_engines_id_and_hangup_ends_it(results):
    assert results["after_stale_hangup"] == 0
    assert results["disconnected"] == 1
    assert results["events"] == [
        ["phone_event", "c1", "ringing"],
        ["phone_event", "c1", "connected"],
        ["phone_event", "c1", "ended"],
    ]


def test_a_call_that_cannot_start_is_reported_failed(results):
    assert results["failed"] == [["c2", "failed", "mic denied"]]
