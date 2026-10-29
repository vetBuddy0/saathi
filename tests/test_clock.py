"""tools/clock.py: what the clock holds, what it tells the face, and the
`clock` tool's answers. Time is a fake clock moved by hand."""

from saathi.tools import clock as clock_module
from saathi.tools.clock import ClockController, done_line, duration_words, make_clock_tool


class FakeTime:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


def _clock():
    now = FakeTime()
    sent: list[dict] = []
    controller = ClockController(clock=now)
    controller.set_broadcast(sent.append)
    return controller, now, sent


def test_durations_are_said_in_words():
    assert duration_words(300) == "5 minutes"
    assert duration_words(5400) == "1 hour 30 minutes"
    assert duration_words(61) == "1 minute 1 second"
    assert duration_words(0) == "0 seconds"
    assert duration_words(5400, before_a_noun=True) == "1-hour 30-minute"


def test_a_timer_shows_the_clock_counts_down_and_is_said_once():
    controller, now, sent = _clock()
    timer = controller.start_timer(300, " tea ")
    assert sent[-1]["visible"] is True
    assert sent[-1]["timers"] == [
        {
            "id": timer.id,
            "label": "tea",
            "seconds": 300,
            "ends_at_ms": round((now.value + 300) * 1000),
            "done": False,
        }
    ]
    assert sent[-1]["now_ms"] == round(now.value * 1000)
    now.value += 299
    assert controller.finished() == []
    now.value += 1
    assert [t.id for t in controller.finished()] == [timer.id]
    assert sent[-1]["timers"][0]["done"] is True
    assert controller.finished() == []  # said once, not every tick
    # Shown as "Time's up" for a while, then gone; the clock stays a
    # little longer, then hides itself.
    now.value += clock_module.DONE_SHOWN
    controller.finished()
    assert sent[-1]["timers"] == [] and sent[-1]["visible"] is True
    now.value += clock_module.IDLE_HIDE
    controller.finished()
    assert sent[-1]["visible"] is False


def test_a_timer_that_ends_with_the_clock_closed_brings_it_back():
    controller, now, sent = _clock()
    controller.start_timer(60)
    controller.hide()
    assert sent[-1]["visible"] is False and len(sent[-1]["timers"]) == 1
    now.value += 60
    assert len(controller.finished()) == 1
    assert sent[-1]["visible"] is True


def test_a_running_timer_or_stopwatch_keeps_the_clock_up():
    controller, now, sent = _clock()
    controller.start_timer(3 * 3600)
    now.value += clock_module.IDLE_HIDE * 10
    controller.finished()
    assert controller.visible
    controller.cancel_timer()
    controller.stopwatch_start()
    now.value += clock_module.IDLE_HIDE * 10
    controller.finished()
    assert controller.visible


def test_cancel_by_label_or_all_and_dismiss_by_tap():
    controller, now, sent = _clock()
    tea = controller.start_timer(60, "tea")
    controller.start_timer(120, "rice")
    assert [t.label for t in controller.cancel_timer("TEA")] == ["tea"]
    assert [t["label"] for t in sent[-1]["timers"]] == ["rice"]
    assert controller.cancel_timer("eggs") == []
    assert len(controller.cancel_timer()) == 1
    assert sent[-1]["timers"] == []
    assert controller.dismiss(tea.id) is False
    egg = controller.start_timer(10, "egg")
    now.value += 10
    controller.finished()
    assert controller.dismiss(egg.id) is True
    assert sent[-1]["timers"] == []


def test_no_more_than_three_timers_at_once():
    controller, _, _ = _clock()
    for _ in range(clock_module.MAX_TIMERS):
        controller.start_timer(60)
    try:
        controller.start_timer(60)
    except ValueError:
        pass
    else:
        raise AssertionError("a fourth timer was started")


def test_the_stopwatch_runs_stops_and_resets():
    controller, now, sent = _clock()
    controller.stopwatch_start()
    assert sent[-1]["stopwatch"] == {
        "running": True,
        "elapsed_ms": 0,
        "since_ms": round(now.value * 1000),
    }
    now.value += 30
    controller.stopwatch_start()  # again: keeps running from the first start
    now.value += 15
    assert controller.stopwatch_stop() == 45
    assert sent[-1]["stopwatch"] == {"running": False, "elapsed_ms": 45000, "since_ms": None}
    controller.stopwatch_start()
    now.value += 5
    assert controller.stopwatch_stop() == 50
    controller.stopwatch_reset()
    assert sent[-1]["stopwatch"] is None


def test_what_saathi_says_when_timers_end():
    controller, now, _ = _clock()
    controller.start_timer(300)
    controller.start_timer(300, "tea")
    now.value += 300
    assert done_line(controller.finished()) == (
        "Your 5-minute timer is done. The tea timer is done."
    )


def test_the_tool_answers_in_words_and_asks_for_a_missing_length():
    controller, now, sent = _clock()
    tool = make_clock_tool(controller)
    assert tool.name == "clock" and tool.permission == "clock"
    shown = tool.handler(action="show")
    assert shown["status"] == "ok" and shown["time"]
    assert sent[-1]["visible"] is True
    assert tool.handler(action="start_timer", minutes=1.5, label="eggs") == {
        "status": "ok",
        "timer": "1 minute 30 seconds",
        "label": "eggs",
    }
    assert tool.handler(action="start_timer")["status"] == "needs_duration"
    assert tool.handler(action="start_timer", minutes=25 * 60)["status"] == "needs_duration"
    assert tool.handler(action="cancel_timer", label="eggs") == {"status": "ok", "cancelled": 1}
    assert tool.handler(action="cancel_timer")["status"] == "not_found"
    assert tool.handler(action="stopwatch_start") == {"status": "ok"}
    now.value += 90
    assert tool.handler(action="stopwatch_stop") == {
        "status": "ok",
        "elapsed": "1 minute 30 seconds",
    }
    assert tool.handler(action="stopwatch_reset") == {"status": "ok"}
    assert tool.handler(action="hide") == {"status": "ok"}
    assert sent[-1]["visible"] is False
    assert tool.handler(action="alarm")["status"] == "error"
