from saathi.core import Core, Event, State


def test_initial_state_is_sleeping():
    assert Core().state == State.SLEEPING


def test_press_from_sleeping_goes_straight_to_listening():
    core = Core()
    assert core.handle(Event("press")) is True
    assert core.state == State.LISTENING


def test_press_from_idle_goes_straight_to_listening():
    core = Core(initial=State.IDLE)
    assert core.handle(Event("press")) is True
    assert core.state == State.LISTENING


def test_release_then_fake_no_response_returns_to_idle():
    core = Core(initial=State.LISTENING)
    assert core.handle(Event("release")) is True
    assert core.state == State.THINKING
    assert core.handle(Event("no_response")) is True
    assert core.state == State.IDLE


def test_barge_in_during_speaking_goes_straight_to_listening():
    core = Core(initial=State.SPEAKING)
    assert core.handle(Event("barge_in")) is True
    assert core.state == State.LISTENING


def test_barge_in_is_a_no_op_outside_speaking():
    core = Core(initial=State.IDLE)
    assert core.handle(Event("barge_in")) is False
    assert core.state == State.IDLE


def test_press_during_speaking_is_barge_in_not_a_no_op():
    # Was a no-op through the one-hour spike (see core.py's module
    # docstring) — an interim guard against the double-sound bug, not
    # the final answer. Checkpoint 2 wires the spacebar as the real
    # interrupt trigger: pressing space while she's speaking now means
    # the same thing "press" means everywhere else it has an edge —
    # start listening — immediately, not after the sentence finishes.
    core = Core(initial=State.SPEAKING)
    assert core.handle(Event("press")) is True
    assert core.state == State.LISTENING


def test_full_table_is_reachable_with_fake_events():
    core = Core(initial=State.SLEEPING)
    assert core.handle(Event("press")) is True
    assert core.state == State.LISTENING
    assert core.handle(Event("release")) is True
    assert core.state == State.THINKING
    assert core.handle(Event("response_ready")) is True
    assert core.state == State.SPEAKING
    assert core.handle(Event("done")) is True
    assert core.state == State.IDLE
    assert core.handle(Event("notice")) is True
    assert core.state == State.ATTENTIVE
    assert core.handle(Event("confirm")) is True
    assert core.state == State.LISTENING
    assert core.handle(Event("handoff")) is True
    assert core.state == State.HANDOFF
    assert core.handle(Event("resolved")) is True
    assert core.state == State.THINKING
    assert core.handle(Event("idle_timeout")) is True
    assert core.state == State.SLEEPING


def test_unknown_event_is_a_no_op():
    core = Core(initial=State.IDLE)
    assert core.handle(Event("gibberish")) is False
    assert core.state == State.IDLE


def test_observer_only_notified_on_actual_change():
    core = Core(initial=State.IDLE)
    seen = []
    core.subscribe(lambda state, event: seen.append((state, event.kind)))

    core.handle(Event("gibberish"))  # no transition -> no notification
    assert seen == []

    core.handle(Event("press"))
    assert seen == [(State.LISTENING, "press")]


def test_unsubscribe_stops_notifications():
    core = Core(initial=State.IDLE)
    seen = []
    unsubscribe = core.subscribe(lambda state, event: seen.append(state))
    unsubscribe()

    core.handle(Event("press"))
    assert seen == []
