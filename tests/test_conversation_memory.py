"""voice/conversation.py: the verbatim window (layer 1) and the running
summary (layer 2). Pure data structure -- no model, no store, no I/O.
"""

from saathi.voice.conversation import (
    MAX_EXCHANGES,
    MAX_TOKENS,
    ConversationMemory,
    Exchange,
    estimate_tokens,
)


def test_a_recorded_exchange_is_sent_verbatim_oldest_first():
    memory = ConversationMemory()
    memory.record("what day is the scan?", "Thursday, at ten.")
    memory.record("and the other one?", "The blood test is Friday.")

    assert memory.messages() == [
        {"role": "user", "content": "what day is the scan?"},
        {"role": "assistant", "content": "Thursday, at ten."},
        {"role": "user", "content": "and the other one?"},
        {"role": "assistant", "content": "The blood test is Friday."},
    ]


def test_window_holds_at_most_max_exchanges_and_evicts_the_oldest():
    memory = ConversationMemory()
    for i in range(MAX_EXCHANGES + 2):
        memory.record(f"u{i}", f"a{i}")

    assert len(memory.window) == MAX_EXCHANGES
    assert memory.window[0] == Exchange(user="u2", assistant="a2")
    # Nothing was dropped on the floor: the two evicted exchanges are
    # waiting to be folded into the summary, in order.
    assert memory.unfolded() == [
        Exchange(user="u0", assistant="a0"),
        Exchange(user="u1", assistant="a1"),
    ]


def test_token_cap_binds_before_the_exchange_cap_does():
    # Two exchanges that together exceed the token budget: the count cap
    # (6) alone would keep both; the token cap evicts the older one.
    memory = ConversationMemory()
    long_text = "x" * (MAX_TOKENS * 4)  # ~MAX_TOKENS tokens by the estimate
    memory.record(long_text, "ok")
    memory.record("short", "reply")

    assert memory.window == [Exchange(user="short", assistant="reply")]
    assert memory.unfolded() == [Exchange(user=long_text, assistant="ok")]


def test_a_single_oversized_exchange_is_never_evicted_from_an_otherwise_empty_window():
    # One enormous turn must still be in the window: the alternative is
    # a model with no verbatim context at all, which is worse than a
    # long prompt once.
    memory = ConversationMemory()
    memory.record("x" * (MAX_TOKENS * 8), "y" * (MAX_TOKENS * 8))

    assert len(memory.window) == 1
    assert memory.unfolded() == []


def test_fold_replaces_the_summary_and_clears_unfolded():
    memory = ConversationMemory(max_exchanges=1)
    memory.record("first", "one")
    memory.record("second", "two")
    assert memory.unfolded() == [Exchange(user="first", assistant="one")]

    memory.fold("  She asked about the first thing; Saathi said one.  ")

    assert memory.summary == "She asked about the first thing; Saathi said one."
    assert memory.unfolded() == []
    # Folding never touches the verbatim window.
    assert memory.window == [Exchange(user="second", assistant="two")]


def test_summary_starts_empty_and_messages_do_not_include_it():
    # The summary is the caller's to place (cascade.py puts it in a
    # system message); messages() is the verbatim window only.
    memory = ConversationMemory()
    assert memory.summary == ""
    memory.record("hi", "hello")
    assert all(m["content"] != "" for m in memory.messages())
    assert len(memory.messages()) == 2


def test_estimate_tokens_is_four_chars_per_token_and_never_zero():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100
    assert Exchange(user="a" * 40, assistant="b" * 80).tokens() == 30
