"""The bench test SPEC.md asks for ("play a known tone, capture, assert
residual below threshold"), plus unit tests for `SystemEchoCancel`'s
parsing/loading against a fake `pactl` — see audio/aec.py's docstring for
why the bench test uses synthetic signals rather than real hardware.
"""

import numpy as np

from saathi.audio.aec import (
    EchoCancelHandles,
    SystemEchoCancel,
    WebrtcAec,
    ensure_echo_cancellation,
)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def _synthetic_echo_scenario(sample_rate: int = 16000, duration_s: float = 1.0):
    """A known tone played through the speaker (`far`), and what the mic
    would pick up (`near`): that same tone, attenuated and delayed as an
    acoustic echo would be, plus background noise."""
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    tone = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)

    rng = np.random.default_rng(0)
    noise = (0.02 * rng.standard_normal(len(t))).astype(np.float32)

    delay = int(sample_rate * 0.04)
    echo = np.zeros_like(tone)
    echo[delay:] = tone[: len(tone) - delay] * 0.8

    return echo + noise, tone  # near, far


def test_bench_webrtc_aec_removes_the_known_tone():
    sample_rate = 16000
    near, far = _synthetic_echo_scenario(sample_rate)

    aec = WebrtcAec(sample_rate=sample_rate)
    frame = int(sample_rate * 0.01)  # 10ms frames
    out = np.concatenate(
        [
            aec.process(near[i : i + frame], far[i : i + frame])
            for i in range(0, len(near) - frame + 1, frame)
        ]
    )

    # Give AEC 200ms to converge before judging the residual.
    skip = int(sample_rate * 0.2)
    near_settled = near[skip:]
    out_settled = out[skip : skip + len(near_settled)]

    assert _rms(near_settled) > 0.05, "scenario has no real echo to cancel"

    residual_ratio = _rms(out_settled) / _rms(near_settled)
    assert residual_ratio < 0.2, f"AEC left {residual_ratio:.0%} of the echo, expected < 20%"


def test_bench_control_without_cancellation_shows_the_full_echo():
    # Same scenario, cancellation turned off: proves the bench test above
    # is actually discriminating, not just measuring something that's
    # always small.
    sample_rate = 16000
    near, far = _synthetic_echo_scenario(sample_rate)

    from pywebrtc_audio import AudioProcessor

    processor = AudioProcessor(
        sample_rate=sample_rate,
        echo_cancellation=False,
        noise_suppression=False,
        auto_gain_control=False,
        stream_delay_ms=40,
    )
    frame = int(sample_rate * 0.01)
    out = np.concatenate(
        [
            processor.process(near[i : i + frame], far[i : i + frame])
            for i in range(0, len(near) - frame + 1, frame)
        ]
    )

    residual_ratio = _rms(out) / _rms(near)
    assert residual_ratio > 0.9


# -- SystemEchoCancel: the "try this first" path -----------------------------


def _filter_block(kind: str, index: int, name: str, module: str, master: str) -> str:
    return (
        f"{kind} #{index}\n"
        f"\tName: {name}\n"
        f"\tOwner Module: {module}\n"
        "\tProperties:\n"
        '\t\tdevice.class = "filter"\n'
        f'\t\tdevice.master_device = "{master}"\n'
    )


class FakePactl:
    """Stands in for a real PulseAudio server: `list sources`/`list
    sinks` reflect whatever pairs exist so far; `load-module` creates one,
    the way PulseAudio itself would."""

    def __init__(self, sources=(), sinks=()):
        self._sources = list(sources)
        self._sinks = list(sinks)
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(list(args))
        if args[:2] == ["list", "sources"]:
            return "\n\n".join(
                _filter_block("Source", i, s["name"], s["module"], s["master"])
                for i, s in enumerate(self._sources, start=1)
            )
        if args[:2] == ["list", "sinks"]:
            return "\n\n".join(
                _filter_block("Sink", i, s["name"], s["module"], s["master"])
                for i, s in enumerate(self._sinks, start=1)
            )
        if args[0] == "load-module":
            kwargs = dict(a.split("=", 1) for a in args if "=" in a)
            module = "99"
            self._sources.append(
                {
                    "name": f"{kwargs['source_master']}.echo-cancel",
                    "module": module,
                    "master": kwargs["source_master"],
                }
            )
            self._sinks.append(
                {
                    "name": f"{kwargs['sink_master']}.echo-cancel",
                    "module": module,
                    "master": kwargs["sink_master"],
                }
            )
            return f"{module}\n"
        return ""


def test_find_returns_the_existing_pair_without_loading_anything():
    pactl = FakePactl(
        sources=[{"name": "mic.echo-cancel", "module": "28", "master": "mic"}],
        sinks=[{"name": "speaker.echo-cancel", "module": "28", "master": "speaker"}],
    )
    system = SystemEchoCancel(run=pactl)

    handles = system.find("mic", "speaker")

    assert handles == EchoCancelHandles(
        module_index="28", source_id="mic.echo-cancel", sink_id="speaker.echo-cancel"
    )
    assert not any(call[0] == "load-module" for call in pactl.calls)


def test_find_returns_none_when_nothing_is_loaded():
    system = SystemEchoCancel(run=FakePactl())
    assert system.find("mic", "speaker") is None


def test_ensure_loads_a_pair_when_none_exists():
    pactl = FakePactl()
    system = SystemEchoCancel(run=pactl)

    handles = system.ensure("mic", "speaker")

    assert handles.source_id == "mic.echo-cancel"
    assert handles.sink_id == "speaker.echo-cancel"
    load_calls = [call for call in pactl.calls if call[0] == "load-module"]
    assert len(load_calls) == 1
    assert "source_master=mic" in load_calls[0]
    assert "sink_master=speaker" in load_calls[0]


def test_find_ignores_a_pair_scoped_to_different_devices():
    # Verified against this dev machine's real PulseAudio: a
    # module-echo-cancel loaded without explicit source_master/sink_master
    # (e.g. by distro default.pa config, not by us) reports device.master
    # pointing at *some* default device, not necessarily our mic/speaker.
    # find() must not claim that pair as a match for a different mic.
    pactl = FakePactl(
        sources=[{"name": "other.echo-cancel", "module": "28", "master": "some_other_sink"}],
        sinks=[{"name": "other.echo-cancel.sink", "module": "28", "master": "some_other_sink"}],
    )
    system = SystemEchoCancel(run=pactl)

    assert system.find("mic", "speaker") is None


def test_ensure_does_not_load_a_second_pair_when_one_already_exists():
    pactl = FakePactl(
        sources=[{"name": "mic.echo-cancel", "module": "28", "master": "mic"}],
        sinks=[{"name": "speaker.echo-cancel", "module": "28", "master": "speaker"}],
    )
    system = SystemEchoCancel(run=pactl)

    system.ensure("mic", "speaker")

    assert not any(call[0] == "load-module" for call in pactl.calls)


def test_unload_calls_pactl_with_the_module_index():
    pactl = FakePactl()
    system = SystemEchoCancel(run=pactl)

    system.unload(EchoCancelHandles(module_index="42", source_id="x", sink_id="y"))

    assert pactl.calls[-1] == ["unload-module", "42"]


def test_ensure_echo_cancellation_falls_back_to_webrtc_aec_when_pactl_is_unavailable():
    def broken(_args):
        raise FileNotFoundError("no pactl on this machine")

    result = ensure_echo_cancellation("mic", "speaker", system=SystemEchoCancel(run=broken))

    assert isinstance(result, WebrtcAec)


def test_ensure_echo_cancellation_prefers_the_system_module_when_available():
    pactl = FakePactl(
        sources=[{"name": "mic.echo-cancel", "module": "28", "master": "mic"}],
        sinks=[{"name": "speaker.echo-cancel", "module": "28", "master": "speaker"}],
    )

    result = ensure_echo_cancellation("mic", "speaker", system=SystemEchoCancel(run=pactl))

    assert isinstance(result, EchoCancelHandles)
