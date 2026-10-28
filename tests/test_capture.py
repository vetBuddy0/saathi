import io

from saathi.audio.capture import read_fixed_chunks


def test_yields_fixed_size_chunks():
    stream = io.BytesIO(b"a" * 10 + b"b" * 10 + b"c" * 10)
    chunks = list(read_fixed_chunks(stream, chunk_bytes=10))
    assert chunks == [b"a" * 10, b"b" * 10, b"c" * 10]


def test_drops_a_trailing_short_read():
    stream = io.BytesIO(b"a" * 10 + b"b" * 4)
    chunks = list(read_fixed_chunks(stream, chunk_bytes=10))
    assert chunks == [b"a" * 10]


def test_empty_stream_yields_nothing():
    assert list(read_fixed_chunks(io.BytesIO(b""), chunk_bytes=10)) == []


def test_parec_asks_for_a_short_fragment_not_the_two_second_default():
    # Without --latency-msec the sound server delivers 2 s bursts (measured
    # 2026-10-07): the wake word and the hands-free endpointer then only
    # hear her on a burst boundary. The fragment must be shorter than one
    # 32 ms VAD frame.
    from saathi.audio.capture import parec_command

    command = parec_command("some-source")
    assert command[0] == "parec"
    latency = [a for a in command if a.startswith("--latency-msec=")]
    assert len(latency) == 1
    assert 0 < int(latency[0].split("=")[1]) <= 32
    assert "--device=some-source" in command
