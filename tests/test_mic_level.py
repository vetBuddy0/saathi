"""audio/level.py: a mic too close to clipping is stepped down until it
isn't, never raised, and never below the floor -- against a fake
recorder whose level depends on the volume the fake pactl holds, the
way the real internal mic's did (2026-10-28)."""

import array

from saathi.audio.level import (
    FLOOR_PERCENT,
    MicLevelGuard,
    hot_fraction,
    parse_volume_percent,
)


def _pcm(samples: list[int]) -> bytes:
    return array.array("h", samples).tobytes()


class _Mixer:
    """A source whose recording is too hot while its volume is above `clips_above`."""

    def __init__(self, volume: int, clips_above: int) -> None:
        self.volume = volume
        self.clips_above = clips_above
        self.sets: list[int] = []

    def pactl(self, args: list[str]) -> str:
        if args[0] == "get-source-volume":
            return (
                f"Volume: front-left: 35028 /  {self.volume}% / -16.32 dB,"
                f"   front-right: 35028 /  {self.volume}% / -16.32 dB\n"
            )
        if args[0] == "set-source-volume":
            assert args[1] == "mic-from-pactl"
            self.volume = int(args[2].rstrip("%"))
            self.sets.append(self.volume)
            return ""
        raise AssertionError(args)

    def record(self, source_id: str, seconds: float) -> bytes:
        assert source_id == "mic-from-pactl"
        if self.volume > self.clips_above:
            return _pcm([32767, -32768, 1200, -900] * 4000)  # half at the rail
        return _pcm([3900, -3900, 120, -80] * 4000)


def test_hot_fraction_counts_both_sides_within_6_db_of_the_rail():
    assert hot_fraction(_pcm([32767, -16384, 0, 100])) == 0.5
    assert hot_fraction(_pcm([16383, -16383])) == 0.0
    assert hot_fraction(b"") == 0.0


def test_volume_percent_is_read_from_pactl_text():
    text = "Volume: front-left: 35028 /  53% / -16.32 dB,   front-right: 34000 /  52% / -17 dB"
    assert parse_volume_percent(text) == 53
    assert parse_volume_percent("") is None


def test_a_clean_mic_is_left_alone():
    mixer = _Mixer(volume=30, clips_above=40)
    assert MicLevelGuard("mic-from-pactl", record=mixer.record, pactl=mixer.pactl).check() is None
    assert mixer.sets == []


def test_a_clipping_mic_is_stepped_down_until_it_stops():
    mixer = _Mixer(volume=53, clips_above=30)
    note = MicLevelGuard("mic-from-pactl", record=mixer.record, pactl=mixer.pactl).check()
    assert mixer.sets == [43, 33, 23]
    assert note is not None and "53%" in note and "23%" in note


def test_it_stops_at_the_floor_and_says_the_hardware_is_suspect():
    mixer = _Mixer(volume=35, clips_above=0)
    note = MicLevelGuard("mic-from-pactl", record=mixer.record, pactl=mixer.pactl).check()
    assert mixer.sets == [25, FLOOR_PERCENT]
    assert note is not None and "still near clipping" in note


def test_an_unreadable_volume_is_reported_not_guessed():
    mixer = _Mixer(volume=53, clips_above=0)
    guard = MicLevelGuard("mic-from-pactl", record=mixer.record, pactl=lambda args: "")
    assert "couldn't be read" in guard.check()
    assert mixer.sets == []
