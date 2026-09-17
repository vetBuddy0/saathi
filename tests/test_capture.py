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
