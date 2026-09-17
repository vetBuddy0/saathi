from saathi.audio.devices import Device, DeviceManager, FakeBackend
from saathi.smoke import main

MIC = Device(id="mic", description="Test Mic", direction="input", bus="usb")
SPEAKER = Device(id="speaker", description="Test Speaker", direction="output", bus="usb")


def test_exits_zero_when_mic_and_speaker_present(capsys):
    manager = DeviceManager(FakeBackend([MIC, SPEAKER]))
    assert main(manager=manager) == 0
    out = capsys.readouterr().out
    assert "Test Mic" in out
    assert "Test Speaker" in out


def test_exits_nonzero_when_no_microphone(capsys):
    manager = DeviceManager(FakeBackend([SPEAKER]))
    assert main(manager=manager) == 1
    assert "No microphone found" in capsys.readouterr().out


def test_exits_nonzero_when_no_speaker(capsys):
    manager = DeviceManager(FakeBackend([MIC]))
    assert main(manager=manager) == 1
    assert "No speaker found" in capsys.readouterr().out
