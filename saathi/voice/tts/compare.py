"""The voice comparison: the same two sentences, English and Mandarin,
through Piper, Google Neural2/WaveNet and Google Chirp3-HD, with
time-to-first-audio measured the one way that matters.

    uv run python -m saathi.voice.tts.compare
    uv run python -m saathi.voice.tts.compare --candidates   # Chirp speakers

Exists because "Piper sounds flat" is a judgement only ears can make,
and ears need the same words through each voice, side by side, in
files that can be replayed. Numbers alone would have been enough for
latency; they are not enough for warmth. The files land in
`~/.saathi/tts-compare/` and are not committed (audio artifacts don't
belong in source control -- `docs/completed/C-tts-backends.md`); the
table is what goes in the doc.

TTFA is measured exactly as `cascade._speak()` measures it: create the
generator, start the clock, pull the first item, stop the clock when
the first WAV is in hand. Nothing else was considered honest: a number
taken any other way would not be the one `turns.first_tts_chunk_ms`
reports, and the latency-budget test reads that column. Every backend
is warmed identically first (`preload()` where it exists, then one
throwaway synthesis in the same language), because the production path
warms at construction and during `end_turn()`'s network wait -- a cold
number would measure model loading and TLS handshakes, not the voice.

One extra column, `first chunk`, for backends that expose
`stream_pcm()`: the time to the first raw audio chunk from Google.
That is what a raw-PCM playback path would turn into TTFA. It is
measured with a separate call so the TTFA column stays uncontaminated
by instrumentation, and it is labelled as a projection, not a result.

The two sentences are things she would actually hear -- a greeting
with a question, and a reminder offer -- not "the quick brown fox".
Mandarin is a translation of the same two, so the pair of files per
backend is the same content in both languages, which is the only way
to hear whether two voices are one person.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from saathi.voice.language import SUPPORTED_LANGUAGES
from saathi.voice.tts import TTSBackend
from saathi.voice.tts.google_backend import GoogleChirp3HDBackend
from saathi.voice.tts.registry import default_backends

DEFAULT_OUT_DIR = Path.home() / ".saathi" / "tts-compare"

COMPARED_BACKEND_IDS = ("piper", "google-neural2", "google-chirp3-hd")

# Same two sentences, translated, not two different scripts.
SENTENCES: dict[str, list[str]] = {
    "english": [
        "Good morning, Auntie, did you sleep well last night?",
        "It's nearly four o'clock, so shall I remind you about your tablets in a little while?",
    ],
    "chinese": [
        "阿姨，早上好，昨晚睡得好吗？",
        "快四点了，等一下要不要我提醒您吃药？",
    ],
}

WARM_UP: dict[str, str] = {"english": "Hello.", "chinese": "你好。"}

# Female Chirp3-HD speakers whose one-word descriptor in Google's own
# voice list fits a companion for someone in her seventies. The default
# (Sulafat, "Warm") is first; see google_backend.py for the reasoning.
CANDIDATE_CHIRP_SPEAKERS = ("Sulafat", "Achernar", "Gacrux", "Vindemiatrix")


@dataclass(frozen=True)
class Measurement:
    backend_id: str
    language: str
    voice: str
    ttfa_ms: int
    total_ms: int
    num_bytes: int
    sample_rate_hz: int
    path: Path
    first_chunk_ms: int | None = None  # projection: see module docstring


def voice_label(backend: TTSBackend, language: str) -> str:
    voice_for = getattr(backend, "voice_for", None)
    if voice_for is not None:
        return voice_for(language)[0]
    if backend.id == "piper":
        return SUPPORTED_LANGUAGES[language]
    return "?"


def warm_up(backend: TTSBackend, language: str) -> None:
    """Identical for every backend: `preload()` if it has one (a
    background thread; the throwaway synthesis below then blocks on its
    lock until the voice is loaded), then one real synthesis, discarded.
    For Google that first call is also what builds the client and opens
    the connection."""
    preload = getattr(backend, "preload", None)
    if preload is not None:
        preload(language)
    list(backend.synthesize_stream(language, [WARM_UP[language]]))


def concat_wavs(wavs: Iterable[bytes]) -> tuple[bytes, int]:
    """One WAV from several with the same format. Returns
    `(wav_bytes, sample_rate_hz)`."""
    params = None
    frames = []
    for wav_bytes in wavs:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            if params is None:
                params = wav_file.getparams()
            frames.append(wav_file.readframes(wav_file.getnframes()))
    if params is None:
        raise ValueError("no WAVs to concatenate")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        out.writeframes(b"".join(frames))
    return buffer.getvalue(), params.framerate


def measure_first_chunk_ms(
    backend: TTSBackend,
    language: str,
    sentence: str,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> int | None:
    stream_pcm = getattr(backend, "stream_pcm", None)
    if stream_pcm is None:
        return None
    chunks = stream_pcm(language, sentence)
    started_at = clock()
    if next(chunks, None) is None:
        return None  # nothing came back; no first chunk to time
    first_chunk_ms = round((clock() - started_at) * 1000)
    for _ in chunks:  # drain, so the stream closes cleanly
        pass
    return first_chunk_ms


def measure(
    backend: TTSBackend,
    language: str,
    sentences: list[str],
    out_path: Path,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Measurement:
    """The `cascade._speak()` protocol, exactly: generator first, then
    the clock, then the first `next()`."""
    stream = backend.synthesize_stream(language, sentences)
    started_at = clock()
    first_wav = next(stream)
    ttfa_ms = round((clock() - started_at) * 1000)
    rest = list(stream)
    total_ms = round((clock() - started_at) * 1000)

    joined, sample_rate_hz = concat_wavs([first_wav, *rest])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(joined)
    return Measurement(
        backend_id=backend.id,
        language=language,
        voice=voice_label(backend, language),
        ttfa_ms=ttfa_ms,
        total_ms=total_ms,
        num_bytes=len(joined),
        sample_rate_hz=sample_rate_hz,
        path=out_path,
    )


def run_comparison(
    backends: dict[str, TTSBackend],
    out_dir: Path,
    *,
    backend_ids: tuple[str, ...] = COMPARED_BACKEND_IDS,
    languages: tuple[str, ...] = ("english", "chinese"),
    log: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> tuple[list[Measurement], list[str]]:
    """Returns `(measurements, skipped)`; a backend that isn't available,
    or that fails mid-render, is reported with its reason, never raised
    on -- the table should still come out for whatever *does* work."""
    measurements: list[Measurement] = []
    skipped: list[str] = []
    for backend_id in backend_ids:
        backend = backends.get(backend_id)
        if backend is None:
            skipped.append(f"{backend_id}: not in the registry")
            continue
        available, reason = backend.available()
        if not available:
            skipped.append(f"{backend_id}: {reason}")
            continue
        for language in languages:
            try:
                log(f"{backend_id} / {language}: warming up")
                warm_up(backend, language)
                out_path = out_dir / f"{backend_id}-{language}.wav"
                log(f"{backend_id} / {language}: rendering -> {out_path}")
                measurement = measure(backend, language, SENTENCES[language], out_path)
                first_chunk_ms = measure_first_chunk_ms(
                    backend, language, SENTENCES[language][0]
                )
            except Exception as exc:  # one backend's failure must not cost the others' rows
                skipped.append(f"{backend_id} / {language}: {type(exc).__name__}: {exc}")
                continue
            if first_chunk_ms is not None:
                measurement = Measurement(
                    **{**measurement.__dict__, "first_chunk_ms": first_chunk_ms}
                )
            measurements.append(measurement)
    return measurements, skipped


def run_candidates(
    out_dir: Path,
    *,
    speakers: tuple[str, ...] = CANDIDATE_CHIRP_SPEAKERS,
    languages: tuple[str, ...] = ("english", "chinese"),
    make_backend: Callable[[str], TTSBackend] = lambda s: GoogleChirp3HDBackend(speaker=s),
    log: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> list[Measurement]:
    """The shortlist for ears: each candidate Chirp3-HD speaker, both
    languages, same sentences. Files land as
    `chirp3-hd-<Speaker>-<language>.wav`."""
    measurements: list[Measurement] = []
    for speaker in speakers:
        backend = make_backend(speaker)
        available, reason = backend.available()
        if not available:
            log(f"{speaker}: skipped, {reason}")
            continue
        for language in languages:
            warm_up(backend, language)
            out_path = out_dir / f"chirp3-hd-{speaker}-{language}.wav"
            log(f"{speaker} / {language}: rendering -> {out_path}")
            measurements.append(measure(backend, language, SENTENCES[language], out_path))
    return measurements


def format_table(measurements: list[Measurement]) -> str:
    header = (
        "backend",
        "language",
        "voice",
        "TTFA ms",
        "first chunk ms*",
        "total ms",
        "bytes",
        "rate",
        "file",
    )
    rows = [
        (
            m.backend_id,
            m.language,
            m.voice,
            str(m.ttfa_ms),
            "-" if m.first_chunk_ms is None else str(m.first_chunk_ms),
            str(m.total_ms),
            str(m.num_bytes),
            str(m.sample_rate_hz),
            m.path.name,
        )
        for m in measurements
    ]
    widths = [max(len(str(cell)) for cell in column) for column in zip(header, *rows)]

    def fmt(row) -> str:
        return "| " + " | ".join(str(cell).ljust(w) for cell, w in zip(row, widths)) + " |"

    lines = [fmt(header), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    lines.extend(fmt(row) for row in rows)
    lines.append(
        "* first chunk: time to Google's first raw PCM chunk (stream_pcm); what a "
        "raw-PCM playback path would make TTFA. A projection, not what cascade "
        "measures today."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="render the Chirp3-HD speaker shortlist instead of the three-backend comparison",
    )
    args = parser.parse_args(argv)

    if args.candidates:
        measurements = run_candidates(args.out_dir)
        skipped: list[str] = []
    else:
        measurements, skipped = run_comparison(default_backends(), args.out_dir)

    print(format_table(measurements))
    for line in skipped:
        print(f"skipped {line}")
    print(f"files: {args.out_dir}")
    return 0 if measurements else 1


if __name__ == "__main__":
    raise SystemExit(main())
