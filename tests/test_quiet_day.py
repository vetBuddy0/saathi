"""initiative/quiet_day.py: no word from her by late morning -> Melody asks
how she is -> no answer -> the family is told, once. A real IdentityStore
on disk and a fake clock; the screen server's half is driven through its
tick with a recording session, so no audio and no push service."""

import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.call.family import Member
from saathi.call.family_runtime import FamilyRuntime
from saathi.call.push import PushError, Subscription
from saathi.core import Core, State
from saathi.identity.preferences import write_preference
from saathi.identity.store import IdentityStore
from saathi.initiative.quiet_day import (
    AWAY_KEY,
    GREETING,
    QuietDayConfig,
    QuietDayWatch,
    Step,
    family_message,
    make_going_away_tool,
)
from saathi.screen.server import QUIET_DAY_TICK, build_app
from tests.test_conversation_window import QUIET, _feed, _window
from tests.test_screen_server import ChunkCapture, RecordingSession, _connect

SGT = timezone(timedelta(hours=8))
DAY = date(2026, 10, 20)


def at(hh: int, mm: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=SGT)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def db() -> Path:
    path = Path(tempfile.mkdtemp()) / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
    return path


def _turn(db: Path, when: datetime) -> None:
    with IdentityStore(db) as store:
        store.append("turns", ts=when.astimezone(timezone.utc).isoformat(), mode="press")


def _rows(db: Path) -> list[dict]:
    with IdentityStore(db) as store:
        return store.read("initiatives", kind="event")


def _watch(db: Path, clock: Clock, tell=None, **config) -> QuietDayWatch:
    return QuietDayWatch(db, QuietDayConfig(**config), tell_family=tell, now=clock)


# -- deciding -------------------------------------------------------------------


def test_nothing_before_the_check_time(db):
    assert _watch(db, Clock(at(10, 59))).decide() is None


def test_no_word_by_eleven_asks_her_then_tells_the_family_after_the_wait(db):
    clock = Clock(at(11, 0))
    watch = _watch(db, clock)
    assert watch.decide() == Step("greet")
    watch.greeted()
    clock.now = at(11, 29)
    assert watch.decide() is None  # still waiting for her answer
    clock.now = at(11, 30)
    assert watch.decide() == Step("notify", "11:00")


def test_a_word_from_her_today_settles_it_even_after_the_greeting(db):
    clock = Clock(at(11, 0))
    watch = _watch(db, clock)
    watch.greeted()
    _turn(db, at(11, 2))  # she answered
    clock.now = at(12, 0)
    assert watch.decide() is None


def test_an_early_morning_turn_counts_and_yesterday_does_not(db):
    _turn(db, at(23, 50, DAY - timedelta(days=1)))
    assert _watch(db, Clock(at(11, 0))).decide() == Step("greet")
    _turn(db, at(7, 15))
    assert _watch(db, Clock(at(11, 0))).decide() is None


def test_a_restart_mid_morning_picks_up_where_it_was(db):
    clock = Clock(at(11, 0))
    _watch(db, clock).greeted()
    clock.now = at(11, 45)
    assert _watch(db, clock).decide() == Step("notify", "11:00")  # a new instance


def test_the_check_time_and_wait_are_config(db):
    clock = Clock(at(9, 30))
    watch = _watch(db, clock, check_at=time(9, 30), wait_minutes=10)
    assert watch.decide() == Step("greet")
    watch.greeted()
    clock.now = at(9, 40)
    assert watch.decide() == Step("notify", "09:30")


# -- telling the family ---------------------------------------------------------


def test_the_family_is_told_once_in_their_own_words_for_her(db):
    sent = []

    def tell(title, body_for):
        sent.append((title, body_for("Mum")))
        return 1

    clock = Clock(at(11, 0))
    watch = _watch(db, clock, tell)
    watch.greeted()
    clock.now = at(11, 30)
    assert watch.notify(watch.decide()) == 1
    assert sent == [
        ("Melody", "Mum hasn't talked to Melody yet today. "
                   "Melody asked how she was at 11:00 and heard no answer.")
    ]
    clock.now = at(15, 0)
    assert watch.decide() is None  # once a day
    notified = [r for r in _rows(db) if "told the family" in r["reason"]]
    assert len(notified) == 1 and notified[0]["suppressed_by"] is None


def test_without_the_family_app_it_is_logged_not_retried(db):
    clock = Clock(at(11, 0))
    watch = _watch(db, clock, tell=None)
    watch.greeted()
    clock.now = at(11, 30)
    assert watch.notify(watch.decide()) == 0
    assert watch.decide() is None
    assert any(r["suppressed_by"] == "the family app is off" for r in _rows(db))


def test_a_push_service_failure_is_logged_not_raised(db):
    def tell(title, body_for):
        raise OSError("unreachable")

    clock = Clock(at(11, 0))
    watch = _watch(db, clock, tell)
    watch.greeted()
    clock.now = at(11, 30)
    assert watch.notify(watch.decide()) == 0
    assert any((r["suppressed_by"] or "").startswith("sending failed") for r in _rows(db))


def test_no_paired_phone_says_so(db):
    clock = Clock(at(11, 0))
    watch = _watch(db, clock, tell=lambda title, body_for: 0)
    watch.greeted()
    clock.now = at(11, 30)
    watch.notify(watch.decide())
    assert any(
        r["suppressed_by"] == "no family phone is paired for notifications" for r in _rows(db)
    )


def test_the_message_never_guesses():
    assert family_message("Nani", "11:00") == (
        "Nani hasn't talked to Melody yet today. "
        "Melody asked how she was at 11:00 and heard no answer."
    )
    assert family_message(None, "11:00").startswith("She hasn't talked to Melody yet today.")


# -- away -----------------------------------------------------------------------


def test_away_holds_the_check_until_the_day_she_gave(db):
    with IdentityStore(db) as store:
        write_preference(store, AWAY_KEY, (DAY + timedelta(days=1)).isoformat())
    clock = Clock(at(11, 0))
    watch = _watch(db, clock)
    step = watch.decide()
    assert step == Step("away", "until 2026-10-21")
    watch.away(step)
    assert watch.decide() is None  # logged once, then quiet for the day
    clock.now = at(11, 0, DAY + timedelta(days=2))
    assert watch.decide() == Step("greet")  # back to normal after


def test_the_going_away_tool_sets_and_clears_it(db):
    tool = make_going_away_tool(db, today=lambda: DAY)
    assert tool.permission == "preferences"
    assert tool.handler(days=3) == {"status": "ok", "away": True, "until": "2026-10-22"}
    assert _watch(db, Clock(at(11, 0, DAY + timedelta(days=2)))).decide().action == "away"
    assert tool.handler(days=0) == {"status": "ok", "away": False}
    assert _watch(db, Clock(at(11, 0))).decide() == Step("greet")
    assert tool.handler(days=31)["status"] == "error"
    assert tool.handler(days="soon")["status"] == "error"


def test_a_held_greeting_is_logged_once_per_reason(db):
    watch = _watch(db, Clock(at(11, 0)))
    for _ in range(5):
        watch.held("a call is on screen")
    held = [r for r in _rows(db) if "held" in r["reason"]]
    assert len(held) == 1 and held[0]["suppressed_by"] == "a call is on screen"
    assert watch.decide() == Step("greet")  # held is not done


# -- config ---------------------------------------------------------------------


def test_config_from_env():
    assert QuietDayConfig.from_env({}) == QuietDayConfig()
    assert QuietDayConfig.from_env({"SAATHI_QUIET_DAY": "off"}) is None
    config = QuietDayConfig.from_env(
        {"SAATHI_QUIET_DAY": "09:30", "SAATHI_QUIET_DAY_WAIT_MINUTES": "45"}
    )
    assert config == QuietDayConfig(check_at=time(9, 30), wait_minutes=45)
    with pytest.raises(ValueError):
        QuietDayConfig.from_env({"SAATHI_QUIET_DAY": "lunchtime"})
    with pytest.raises(ValueError):
        QuietDayConfig.from_env({"SAATHI_QUIET_DAY_WAIT_MINUTES": "0"})


# -- the family app's half ------------------------------------------------------


class FakeRegistry:
    def __init__(self, members):
        self._members = members

    def members(self):
        return self._members


class FakePush:
    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    def send(self, subscription, payload, **kwargs):
        if subscription.endpoint in self.fail_for:
            raise PushError("gone", 410)
        self.sent.append((subscription.endpoint, payload, kwargs))


def _member(name, calls_her, endpoint):
    sub = Subscription(endpoint, b"\x04" + b"\x00" * 64, b"\x00" * 16) if endpoint else None
    return Member(id=name, name=name, relation=None, subscription=sub, calls_her=calls_her)


def test_tell_family_reaches_each_phone_with_notifications_in_their_words():
    push = FakePush(fail_for={"https://push.example/c"})
    runtime = FamilyRuntime(
        registry=FakeRegistry([
            _member("Udhi", "Amma", "https://push.example/a"),
            _member("Priya", None, None),  # never allowed notifications
            _member("Ravi", "Mum", "https://push.example/c"),  # phone gone
        ]),
        calls=None,
        server=None,
        route=None,
        stable_url=None,
        tunnel=None,
    )
    runtime.push = push
    runtime.public_url = lambda: "https://melody.example"
    reached = runtime.tell_family("Melody", lambda who: f"{who} is quiet")
    assert reached == 1
    [(endpoint, payload, kwargs)] = push.sent
    assert endpoint == "https://push.example/a"
    assert payload["kind"] == "notice"
    assert payload["body"] == "Amma is quiet"
    assert payload["url"] == "https://melody.example/family/"
    assert kwargs["urgency"] == "high"


# -- the screen server's half ---------------------------------------------------


class FakeWatch:
    greeting = GREETING

    def __init__(self, step):
        self.step = step
        self.log = []

    def decide(self):
        step, self.step = self.step, None
        return step

    def greeted(self):
        self.log.append("greeted")

    def held(self, why):
        self.log.append(f"held: {why}")

    def notify(self, step):
        self.log.append(f"notify {step.detail}")
        return 1

    def away(self, step):
        self.log.append("away")


async def _next_state(ws) -> str:
    while True:
        message = await ws.receive_json()
        if message["type"] == "state":
            return message["state"]


async def test_the_greeting_is_spoken_then_she_can_just_answer(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = RecordingSession()
    watch = FakeWatch(Step("greet"))
    app = build_app(
        Core(initial=State.IDLE),
        session=session,
        capture_source_id="src",
        follow_up_seconds=0.5,
        listen_window=_window,
        quiet_day=watch,
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await app[QUIET_DAY_TICK]()
            states = [await _next_state(ws) for _ in range(3)]
            assert states == ["attentive", "speaking", "attentive"]  # eyes on her, listening
            window = ChunkCapture.instances[-1]
            assert window.started and not window.stopped
            _feed(window, *[QUIET] * 20)  # no answer: the window closes
            assert await _next_state(ws) == "idle"
    assert session.spoken == [GREETING]
    assert watch.log == ["greeted"]


async def test_mid_turn_the_greeting_is_held_not_spoken():
    session = RecordingSession()
    watch = FakeWatch(Step("greet"))
    app = build_app(Core(initial=State.THINKING), session=session, quiet_day=watch)
    async with TestClient(TestServer(app)):
        await app[QUIET_DAY_TICK]()
    assert session.spoken == []
    assert watch.log == ["held: she is busy (thinking)"]


async def test_notify_and_away_go_to_the_watch():
    for step, expected in ((Step("notify", "11:00"), "notify 11:00"), (Step("away"), "away")):
        watch = FakeWatch(step)
        app = build_app(Core(initial=State.IDLE), session=RecordingSession(), quiet_day=watch)
        async with TestClient(TestServer(app)):
            await app[QUIET_DAY_TICK]()
        assert watch.log == [expected]


# -- saathi run's wiring --------------------------------------------------------


def test_saathi_run_builds_the_watch_only_with_a_voice_and_unless_switched_off(monkeypatch, db):
    from types import SimpleNamespace

    from saathi import cli

    family = SimpleNamespace(tell_family=lambda title, body_for: 1)

    def runtime(session=object(), family=family):
        return SimpleNamespace(
            session=session,
            family=family,
            notes=[],
            config=SimpleNamespace(identity_db_path=db),
        )

    monkeypatch.delenv("SAATHI_QUIET_DAY", raising=False)
    on = runtime()
    watch = cli._quiet_day(on)
    assert isinstance(watch, QuietDayWatch) and watch._tell_family is family.tell_family
    assert any("Quiet-day check on: at 11:00" in note for note in on.notes)

    no_family = runtime(family=None)
    assert cli._quiet_day(no_family)._tell_family is None
    assert any("nobody can be told" in note for note in no_family.notes)

    assert cli._quiet_day(runtime(session=None)) is None
    monkeypatch.setenv("SAATHI_QUIET_DAY", "off")
    assert cli._quiet_day(runtime()) is None
    monkeypatch.setenv("SAATHI_QUIET_DAY", "noon-ish")
    bad = runtime()
    assert cli._quiet_day(bad) is None
    assert any(note.startswith("Quiet-day check off:") for note in bad.notes)


def test_today_state_for_the_family(db):
    from saathi.initiative.quiet_day import today_state

    clock = Clock(at(11, 0))
    watch = _watch(db, clock)
    assert today_state(db, at(10, 0)) is None
    watch.greeted()
    assert today_state(db, at(11, 5)) == "Melody has asked how she is; no answer yet."
    clock.now = at(11, 30)
    watch.notify(watch.decide())
    assert today_state(db, at(11, 31)).startswith("Melody asked how she was")
    _turn(db, at(12, 0))
    assert today_state(db, at(12, 1)) is None  # she talked: nothing to worry about
