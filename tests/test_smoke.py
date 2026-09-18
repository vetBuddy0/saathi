import pytest

from saathi.audio.devices import Device, DeviceManager, FakeBackend
from saathi.smoke import _report_groq_key, main

MIC = Device(id="mic", description="Test Mic", direction="input", bus="usb")
SPEAKER = Device(id="speaker", description="Test Speaker", direction="output", bus="usb")


@pytest.fixture(autouse=True)
def no_real_groq_key(monkeypatch):
    # main() now also checks GROQ_API_KEY — these tests are about the
    # device inventory, not the key, so keep the real environment (which
    # may or may not have one set) from leaking in and making them flaky.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


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


# -- GROQ_API_KEY verification -------------------------------------------


class _FakeModels:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self._raise_exc = raise_exc
        self.list_calls = 0

    def list(self):
        self.list_calls += 1
        if self._raise_exc:
            raise self._raise_exc
        return ["some-model"]


class _FakeGroqClient:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.models = _FakeModels(raise_exc)


def test_no_key_set_is_not_a_failure(capsys):
    assert _report_groq_key(api_key=None) is True
    assert "GROQ_API_KEY not set" in capsys.readouterr().out


def test_valid_key_passes_with_one_cheap_call(capsys):
    client = _FakeGroqClient()
    assert _report_groq_key(api_key="gsk_valid", client_factory=lambda api_key: client) is True
    assert client.models.list_calls == 1
    assert "accepted" in capsys.readouterr().out


def test_invalid_key_fails_loudly(capsys):
    client = _FakeGroqClient(raise_exc=RuntimeError("401 Unauthorized"))
    assert _report_groq_key(api_key="gsk_bad", client_factory=lambda api_key: client) is False
    out = capsys.readouterr().out
    assert "rejected" in out
    assert "401 Unauthorized" in out


def test_main_fails_when_key_is_invalid_even_with_hardware_present(capsys, monkeypatch):
    import saathi.smoke as smoke_module

    manager = DeviceManager(FakeBackend([MIC, SPEAKER]))
    client = _FakeGroqClient(raise_exc=RuntimeError("401 Unauthorized"))
    monkeypatch.setattr(
        smoke_module,
        "_report_groq_key",
        lambda: _report_groq_key(api_key="gsk_bad", client_factory=lambda api_key: client),
    )

    assert main(manager=manager) == 1
