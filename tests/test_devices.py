from saathi.audio.devices import Device, DeviceManager, FakeBackend

USB_MIC = Device(id="usb-mic", description="USB Mic", direction="input", bus="usb")
ONBOARD_MIC = Device(id="onboard-mic", description="Onboard Mic", direction="input", bus="pci")
ONBOARD_SPEAKER = Device(
    id="onboard-speaker", description="Onboard Speaker", direction="output", bus="pci"
)


def test_enumerate_filters_by_direction():
    backend = FakeBackend([USB_MIC, ONBOARD_SPEAKER])
    manager = DeviceManager(backend)
    assert manager.enumerate("input") == [USB_MIC]
    assert manager.enumerate("output") == [ONBOARD_SPEAKER]


def test_choose_prefers_usb_over_onboard():
    backend = FakeBackend([ONBOARD_MIC, USB_MIC])
    manager = DeviceManager(backend)
    assert manager.choose("input") == USB_MIC


def test_choose_returns_none_when_nothing_plugged_in():
    manager = DeviceManager(FakeBackend([]))
    assert manager.choose("input") is None


def test_choose_prefers_a_device_that_worked_before_even_if_onboard(tmp_path):
    memory_path = tmp_path / "working.json"
    manager = DeviceManager(FakeBackend([ONBOARD_MIC, USB_MIC]), memory_path=memory_path)
    manager.mark_working(ONBOARD_MIC)
    assert manager.choose("input") == ONBOARD_MIC


def test_working_device_memory_persists_across_managers(tmp_path):
    memory_path = tmp_path / "working.json"
    first = DeviceManager(FakeBackend([ONBOARD_MIC, USB_MIC]), memory_path=memory_path)
    first.mark_working(ONBOARD_MIC)

    second = DeviceManager(FakeBackend([ONBOARD_MIC, USB_MIC]), memory_path=memory_path)
    assert second.choose("input") == ONBOARD_MIC


def test_hotplug_calls_back_with_fresh_device_list():
    backend = FakeBackend([ONBOARD_MIC])
    manager = DeviceManager(backend)
    seen = []
    manager.start_hotplug(seen.append)

    backend.set_devices([ONBOARD_MIC, USB_MIC])
    assert seen == [[ONBOARD_MIC, USB_MIC]]


def test_hotplug_stop_unsubscribes():
    backend = FakeBackend([ONBOARD_MIC])
    manager = DeviceManager(backend)
    seen = []
    stop = manager.start_hotplug(seen.append)
    stop()

    backend.set_devices([])
    assert seen == []
