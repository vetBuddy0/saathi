"""`saathi/voice/tts/compare.py`'s own tests: the measurement protocol
(generator before clock, first `next()` stops it -- the same order
`cascade._speak()` uses), identical warm-up, WAV concatenation, the
skip path for unavailable backends, and the table. No real backend,
no network: fakes that sleep where a real voice would render.
"""

from __future__ import annotations

import io
import wave
from typing import Iterator

from saathi.voice.tts import TTSBackend
from saathi.voice.tts.compare import (
    COMPARED_BACKEND_IDS,
    SENTENCES,
    Measurement,
    concat_wavs,
    format_table,
    measure,
    measure_first_chunk_ms,
    run_candidates,
    run_comparison,
    voice_label,
    warm_up,
)
from saathi.voice.tts.google_backend import pcm_to_wav


def _wav(frames: int, rate: int = 24000) -> bytes:
    return pcm_to_wav(b"\x00\x00" * frames, rate)


class FakeClock:
    """A clock the fakes advance instead of sleeping, so timing
    assertions are exact rather than wall-clock bounds that flake on a
    loaded arm64 runner."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SlowFakeBackend(TTSBackend):
    """No `preload()` on purpose -- the base case is a backend without
    one (Google). `PreloadingFakeBackend` adds it."""

    id = "fake"
    display_name = "Fake"
    license = "n/a"
    local = True

    def __init__(
        self,
        first_delay_s: float = 0.05,
        per_sentence_delay_s: float = 0.02,
        clock: FakeClock | None = None,
    ) -> None:
        self.first_delay_s = first_delay_s
        self.per_sentence_delay_s = per_sentence_delay_s
        self.clock = clock or FakeClock()
        self.calls: list[tuple[str, list[str]]] = []

    def available(self) -> tuple[bool, str]:
        return True, ""

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        self.calls.append((language, list(sentences)))
        for index, _sentence in enumerate(sentences):
            self.clock.advance(self.first_delay_s if index == 0 else self.per_sentence_delay_s)
            yield _wav(240)

    def cost_per_million_chars_usd(self) -> float:
        return 0.0


class PreloadingFakeBackend(SlowFakeBackend):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.preloaded: list[str] = []

    def preload(self, language: str) -> None:
        self.preloaded.append(language)


class StreamingFakeBackend(SlowFakeBackend):
    id = "fake-streaming"

    def voice_for(self, language: str) -> tuple[str, str]:
        return f"xx-{language}-Voice", "xx"

    def stream_pcm(self, language: str, sentence: str) -> Iterator[bytes]:
        self.clock.advance(0.01)
        yield b"\x00\x00" * 100
        self.clock.advance(0.03)
        yield b"\x00\x00" * 100


class FailingFakeBackend(SlowFakeBackend):
    id = "failing"

    def synthesize_stream(self, language, sentences):
        raise RuntimeError("network gone")
        yield  # pragma: no cover


class UnavailableFakeBackend(SlowFakeBackend):
    id = "unavailable"

    def available(self) -> tuple[bool, str]:
        return False, "no credentials"

    def synthesize_stream(self, language, sentences):
        raise AssertionError("must never be called")


def test_measure_times_the_first_next_only_and_writes_one_joined_wav(tmp_path):
    backend = SlowFakeBackend(first_delay_s=0.05, per_sentence_delay_s=0.07)
    out = tmp_path / "fake-english.wav"

    m = measure(backend, "english", ["One.", "Two."], out, clock=backend.clock)

    assert m.ttfa_ms == 50  # first sentence only, not both
    assert m.total_ms == 120
    assert m.backend_id == "fake"
    assert m.sample_rate_hz == 24000
    assert m.path == out and out.exists()
    with wave.open(io.BytesIO(out.read_bytes()), "rb") as wav_file:
        assert wav_file.getnframes() == 480  # both sentences, joined
    assert m.num_bytes == len(out.read_bytes())


def test_measure_starts_the_clock_after_the_generator_is_created_like_cascade_does(tmp_path):
    # The protocol cascade._speak() uses: `stream = synthesize_stream()`,
    # *then* `speak_started_at = monotonic()`, then the first pull. A
    # backend that did real work at generator-creation time would show
    # it here as a clock that started too late -- so the clock is
    # injected and its call order asserted directly.
    ticks: list[str] = []
    real = FakeClock()

    def clock() -> float:
        ticks.append("clock")
        return real()

    class EagerBackend(SlowFakeBackend):
        def synthesize_stream(self, language, sentences):
            ticks.append("generator-created")
            return super().synthesize_stream(language, sentences)

    measure(EagerBackend(0, 0), "english", ["One."], tmp_path / "x.wav", clock=clock)
    assert ticks[:2] == ["generator-created", "clock"]


def test_warm_up_preloads_when_available_and_does_one_throwaway_synthesis():
    backend = PreloadingFakeBackend(0, 0)
    warm_up(backend, "chinese")
    assert backend.preloaded == ["chinese"]
    assert backend.calls == [("chinese", ["你好。"])]


def test_warm_up_works_on_a_backend_without_preload():
    backend = SlowFakeBackend(0, 0)
    assert not hasattr(backend, "preload")  # the branch under test is the getattr miss
    warm_up(backend, "english")
    assert backend.calls == [("english", ["Hello."])]


def test_measure_first_chunk_is_none_without_stream_pcm_and_first_chunk_only_with_it():
    assert measure_first_chunk_ms(SlowFakeBackend(0, 0), "english", "Hi.") is None
    backend = StreamingFakeBackend(0, 0)
    ms = measure_first_chunk_ms(backend, "english", "Hi.", clock=backend.clock)
    assert ms == 10  # the first chunk's 10 ms, not the second's 30


def test_measure_first_chunk_is_none_when_the_stream_yields_nothing():
    class EmptyStream(StreamingFakeBackend):
        def stream_pcm(self, language, sentence):
            return iter(())

    assert measure_first_chunk_ms(EmptyStream(0, 0), "english", "Hi.") is None


def test_concat_wavs_sums_frames_and_keeps_the_rate():
    joined, rate = concat_wavs([_wav(100, 22050), _wav(50, 22050)])
    assert rate == 22050
    with wave.open(io.BytesIO(joined), "rb") as wav_file:
        assert wav_file.getnframes() == 150
        assert wav_file.getframerate() == 22050


def test_run_comparison_skips_unavailable_backends_with_their_reason_and_renders_the_rest(
    tmp_path,
):
    fake = PreloadingFakeBackend(0, 0)
    streaming = StreamingFakeBackend(0, 0)
    backends = {
        "fake": fake,
        "fake-streaming": streaming,
        "unavailable": UnavailableFakeBackend(),
        "failing": FailingFakeBackend(),
    }
    logged: list[str] = []

    measurements, skipped = run_comparison(
        backends,
        tmp_path,
        backend_ids=("fake", "fake-streaming", "unavailable", "missing", "failing"),
        log=logged.append,
    )

    assert skipped == [
        "unavailable: no credentials",
        "missing: not in the registry",
        "failing / english: RuntimeError: network gone",
        "failing / chinese: RuntimeError: network gone",
    ]
    assert [(m.backend_id, m.language) for m in measurements] == [
        ("fake", "english"),
        ("fake", "chinese"),
        ("fake-streaming", "english"),
        ("fake-streaming", "chinese"),
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "fake-chinese.wav",
        "fake-english.wav",
        "fake-streaming-chinese.wav",
        "fake-streaming-english.wav",
    ]
    # Same sentences for every backend, warm-up first, per language.
    assert fake.calls == [
        ("english", ["Hello."]),
        ("english", SENTENCES["english"]),
        ("chinese", ["你好。"]),
        ("chinese", SENTENCES["chinese"]),
    ]
    assert fake.preloaded == ["english", "chinese"]
    assert [m.first_chunk_ms is not None for m in measurements] == [False, False, True, True]
    assert measurements[2].voice == "xx-english-Voice"


def test_voice_label_falls_back_to_pipers_voice_table():
    class PiperLike(SlowFakeBackend):
        id = "piper"

    assert voice_label(PiperLike(0, 0), "chinese") == "zh_CN-huayan-medium"


def test_run_candidates_renders_each_speaker_in_both_languages(tmp_path):
    made: list[str] = []

    def make(speaker: str) -> TTSBackend:
        made.append(speaker)
        backend = StreamingFakeBackend(0, 0)
        backend.id = f"chirp-{speaker}"
        return backend

    measurements = run_candidates(
        tmp_path, speakers=("A", "B"), make_backend=make, log=lambda _line: None
    )
    assert made == ["A", "B"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "chirp3-hd-A-chinese.wav",
        "chirp3-hd-A-english.wav",
        "chirp3-hd-B-chinese.wav",
        "chirp3-hd-B-english.wav",
    ]
    assert len(measurements) == 4


def test_format_table_has_the_required_columns_and_marks_the_projection(tmp_path):
    m = Measurement("piper", "english", "en_US-amy-medium", 123, 456, 7890, 22050, tmp_path / "f")
    table = format_table([m])
    for column in ("backend", "language", "voice", "TTFA ms", "bytes", "rate"):
        assert column in table
    assert "| piper" in table and "22050" in table and "en_US-amy-medium" in table
    assert "projection" in table  # first-chunk is labelled as such, never sold as TTFA


def test_the_three_compared_backends_are_the_brief_s_three():
    assert COMPARED_BACKEND_IDS == ("piper", "google-neural2", "google-chirp3-hd")


def test_sentences_are_the_same_two_in_both_languages():
    assert set(SENTENCES) == {"english", "chinese"}
    assert len(SENTENCES["english"]) == len(SENTENCES["chinese"]) == 2
