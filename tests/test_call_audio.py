"""saathi/call/audio.py — the sink writer against a real child process
(`cat` into a file, not a mocked subprocess), and the bridge against a
fake capture."""

import subprocess
from pathlib import Path

import numpy as np

from saathi.call.audio import OUTBOUND_CHUNK_BYTES, ULAW_FRAME_BYTES, CallAudioBridge, PcmSinkWriter
from saathi.call.codec import pcm16_to_ulaw, ulaw_to_pcm16


def _cat_into(path: Path):
    def popen(_args):
        return subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=open(path, "wb"))

    return popen


def test_sink_writer_streams_bytes_to_its_child_and_closes_cleanly(tmp_path):
    out = tmp_path / "sink.raw"
    writer = PcmSinkWriter("ec-sink-from-aec", popen=_cat_into(out))
    writer.open()
    assert writer.write(b"\x01\x02" * 80)
    assert writer.write(b"\x03\x04" * 80)
    writer.close()
    assert out.read_bytes() == b"\x01\x02" * 80 + b"\x03\x04" * 80
    assert writer.bytes_written == 320


def test_sink_writer_reports_false_after_close_and_before_open(tmp_path):
    writer = PcmSinkWriter("ec-sink", popen=_cat_into(tmp_path / "x.raw"))
    assert writer.write(b"\x00\x00") is False
    writer.open()
    writer.close()
    assert writer.write(b"\x00\x00") is False


def test_sink_writer_command_targets_the_given_sink_at_8k_mono():
    writer = PcmSinkWriter("the-ec-sink")
    command = writer.command
    assert command[0] == "pacat"
    assert "--device=the-ec-sink" in command
    assert "--rate=8000" in command
    assert "--channels=1" in command
    assert "--format=s16le" in command


class FakeCapture:
    instances: list["FakeCapture"] = []

    def __init__(self, source_id, on_chunk, chunk_bytes):
        self.source_id = source_id
        self.on_chunk = on_chunk
        self.chunk_bytes = chunk_bytes
        self.started = False
        self.stopped = False
        FakeCapture.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeWriter:
    def __init__(self, sink_id):
        self.sink_id = sink_id
        self.chunks: list[bytes] = []
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True

    def write(self, pcm):
        self.chunks.append(pcm)
        return True

    def close(self):
        self.closed = True


def _bridge():
    FakeCapture.instances.clear()
    writers = []

    def writer_factory(sink_id):
        writer = FakeWriter(sink_id)
        writers.append(writer)
        return writer

    bridge = CallAudioBridge(
        "ec-source", "ec-sink", capture_factory=FakeCapture, writer_factory=writer_factory
    )
    return bridge, writers


def test_bridge_sends_one_ulaw_frame_per_20ms_capture_chunk():
    bridge, _ = _bridge()
    sent = []
    bridge.start(sent.append)
    capture = FakeCapture.instances[-1]
    assert capture.source_id == "ec-source"
    assert capture.chunk_bytes == OUTBOUND_CHUNK_BYTES == 640
    t = np.arange(320) / 16000
    chunk = (np.sin(2 * np.pi * 440 * t) * 10000).astype("<i2").tobytes()
    capture.on_chunk(chunk)
    assert len(sent) == 1
    assert len(sent[0]) == ULAW_FRAME_BYTES == 160
    assert bridge.stats.frames_out == 1
    assert bridge.stats.rms_out > 1000


def test_bridge_decodes_inbound_frames_into_the_sink_writer():
    bridge, writers = _bridge()
    bridge.start(lambda _ulaw: None)
    assert writers[0].sink_id == "ec-sink" and writers[0].opened
    pcm = (np.full(160, 4000)).astype("<i2").tobytes()
    bridge.feed_inbound(pcm16_to_ulaw(pcm))
    assert len(writers[0].chunks) == 1
    assert writers[0].chunks[0] == ulaw_to_pcm16(pcm16_to_ulaw(pcm))
    assert bridge.stats.frames_in == 1


def test_bridge_stop_releases_capture_and_writer_and_drops_late_chunks():
    bridge, writers = _bridge()
    sent = []
    bridge.start(sent.append)
    capture = FakeCapture.instances[-1]
    bridge.stop()
    assert capture.stopped and writers[0].closed
    capture.on_chunk(b"\x00" * 640)  # a chunk still in flight on the capture thread
    assert sent == []


def test_injected_audio_replaces_the_mic_for_its_duration_then_mic_resumes():
    bridge, _ = _bridge()
    sent = []
    bridge.start(sent.append)
    capture = FakeCapture.instances[-1]
    loud = (np.full(640, 8000)).astype("<i2").tobytes()  # two 20 ms chunks of 16 kHz
    bridge.inject(loud)
    silence = b"\x00" * 640
    capture.on_chunk(silence)
    capture.on_chunk(silence)
    capture.on_chunk(silence)
    assert len(sent) == 3
    assert sent[0] == sent[1] != pcm16_to_ulaw(b"\x00" * 320)
    assert sent[2] == pcm16_to_ulaw(b"\x00" * 320)
