from saathi.core import Core, Event, State


def test_initial_state_is_sleeping():
    assert Core().state == State.SLEEPING


def test_press_from_sleeping_goes_straight_to_listening():
    core = Core()
    assert core.handle(Event("press")) == State.LISTENING


def test_press_from_idle_goes_straight_to_listening():
    core = Core(initial=State.IDLE)
    assert core.handle(Event("press")) == State.LISTENING


def test_release_then_fake_no_response_returns_to_idle():
    core = Core(initial=State.LISTENING)
    assert core.handle(Event("release")) == State.THINKING
    assert core.handle(Event("no_response")) == State.IDLE


def test_barge_in_during_speaking_goes_straight_to_listening():
    core = Core(initial=State.SPEAKING)
    assert core.handle(Event("barge_in")) == State.LISTENING


def test_barge_in_is_a_no_op_outside_speaking():
    core = Core(initial=State.IDLE)
    assert core.handle(Event("barge_in")) == State.IDLE


def test_full_table_is_reachable_with_fake_events():
    core = Core(initial=State.SLEEPING)
    assert core.handle(Event("press")) == State.LISTENING
    assert core.handle(Event("release")) == State.THINKING
    assert core.handle(Event("response_ready")) == State.SPEAKING
    assert core.handle(Event("done")) == State.IDLE
    assert core.handle(Event("notice")) == State.ATTENTIVE
    assert core.handle(Event("confirm")) == State.LISTENING
    assert core.handle(Event("handoff")) == State.HANDOFF
    assert core.handle(Event("resolved")) == State.THINKING
    assert core.handle(Event("idle_timeout")) == State.SLEEPING


def test_unknown_event_is_a_no_op():
    core = Core(initial=State.IDLE)
    assert core.handle(Event("gibberish")) == State.IDLE


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
