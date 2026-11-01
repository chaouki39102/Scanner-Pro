# -*- coding: utf-8 -*-
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scanner_backend as sb


@pytest.fixture(autouse=True)
def clear_last_error():
    sb._LAST_ERROR = None
    yield
    sb._LAST_ERROR = None


class FakeCom:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def CoInitialize(self):
        with self.lock:
            self.events.append(("init", threading.get_ident()))

    def CoUninitialize(self):
        with self.lock:
            self.events.append(("uninit", threading.get_ident()))


class FakeProp:
    def __init__(self, property_id, value=None, values=None, minimum=None, maximum=None, step=None, readonly=False):
        self.PropertyID = property_id
        if value is not None:
            self.Value = value
        if values is not None:
            self.SubTypeValues = values
        if minimum is not None:
            self.SubTypeMin = minimum
        if maximum is not None:
            self.SubTypeMax = maximum
        if step is not None:
            self.SubTypeStep = step
        if readonly:
            self.IsReadOnly = True


class FakeProperties:
    def __init__(self, props=None):
        self._props = list(props or [])

    def __iter__(self):
        return iter(self._props)


class FakeImageFile:
    def __init__(self, payload=b"fake-image"):
        self.payload = payload
        self.file_data = FakeFileData(self.payload)

    @property
    def FileData(self):
        return self.file_data

    def SaveFile(self, path):
        with open(path, "wb") as stream:
            stream.write(self.payload)


class FakeFileData:
    def __init__(self, payload):
        self.BinaryData = payload


class BadSaveImageFile(FakeImageFile):
    def SaveFile(self, path):
        raise RuntimeError("SaveFile unavailable")


class ComError(Exception):
    def __init__(self, hresult, message="com error"):
        super().__init__(message)
        self.hresult = hresult


class FakeItem:
    def __init__(self, props=None, fail_after=None, payload=b"fake-image"):
        self.Properties = FakeProperties(props)
        self.transfers = []
        self.fail_after = fail_after
        self.payload = payload

    def Transfer(self, guid=None):
        self.transfers.append(guid)
        if self.fail_after is not None and len(self.transfers) > self.fail_after:
            raise ComError(sb.WIA_PAPER_EMPTY, "paper empty")
        return FakeImageFile(self.payload)


class FakeDevice:
    def __init__(self, item, props=None):
        self._item = item
        self.Properties = FakeProperties(props)

    def Items(self, index=1):
        return self._item


class FakeInfo:
    def __init__(self, device, props=None, name="Fake Scanner"):
        self._device = device
        self.Properties = FakeProperties(props)
        self.Name = name

    def Connect(self):
        return self._device


class FakeManager:
    def __init__(self, infos):
        self.DeviceInfos = list(infos)


def scanner_info(item=None, *, info_props=None, device_props=None, item_props=None, name="Canon MF3010"):
    item = item or FakeItem(item_props)
    device = FakeDevice(item, device_props)
    return FakeInfo(device, info_props, name=name)


def default_device_props():
    return [
        FakeProp(sb.WIA_DPS_HORIZONTAL_BED_SIZE, 8267),
        FakeProp(sb.WIA_DPS_VERTICAL_BED_SIZE, 11692),
        FakeProp(sb.WIA_DPS_DOCUMENT_HANDLING_SELECT, 2, values=(1, 2, 4)),
    ]


def default_item_props():
    return [
        FakeProp(sb.WIA_IPA_DATATYPE, 3, values=(2, 3)),
        FakeProp(sb.WIA_IP_INTENT, 1),
        FakeProp(sb.WIA_IPA_XRESOLUTION, 300, minimum=50, maximum=2400, step=1),
        FakeProp(sb.WIA_IPA_YRESOLUTION, 300, minimum=50, maximum=2400, step=1),
        FakeProp(sb.WIA_IPA_XPOSITION, 0, minimum=0, maximum=10000),
        FakeProp(sb.WIA_IPA_YPOSITION, 0, minimum=0, maximum=10000),
        FakeProp(sb.WIA_IPA_XEXTENT, 2480, minimum=1, maximum=10000, step=1),
        FakeProp(sb.WIA_IPA_YEXTENT, 3508, minimum=1, maximum=20000, step=1),
    ]


def test_com_apartment_initialises_once_per_thread_and_balances_on_exception():
    fake = FakeCom()

    def worker():
        try:
            with sb.com_apartment(fake):
                with sb.com_apartment(fake):
                    pass
                raise RuntimeError("expected")
        except RuntimeError:
            pass

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert [event for event, _tid in fake.events] == ["init", "uninit"]
    thread_ids = {tid for _event, tid in fake.events}
    assert len(thread_ids) == 1
    assert thread_ids != {threading.get_ident()}


def test_com_apartment_is_balanced_for_each_concurrent_worker():
    fake = FakeCom()

    def worker():
        with sb.com_apartment(fake):
            pass

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert [event for event, _tid in fake.events].count("init") == 3
    assert [event for event, _tid in fake.events].count("uninit") == 3
    assert len({tid for _event, tid in fake.events}) == 3


def test_wia_manager_is_released_before_com_uninitialise():
    fake = FakeCom()
    info = scanner_info(name="Release Tracker")

    class TrackingManager:
        def __init__(self, infos):
            self.DeviceInfos = list(infos)

        def __del__(self):
            fake.events.append(("release", threading.get_ident()))

    devices = sb.list_wia_devices(
        dispatch=lambda _prog_id: TrackingManager([info]),
        com_module=fake,
    )

    assert len(devices) == 1
    events = [event for event, _tid in fake.events]
    assert events == ["init", "release", "uninit"]


def test_clamp_property_value_uses_driver_list_and_range():
    listed = FakeProp(1, values=(75, 150, 300, 600))
    assert sb.clamp_property_value(listed, 200, prefer_ceil=True) == 300
    assert sb.clamp_property_value(listed, 200, prefer_ceil=False) == 150
    assert sb.clamp_property_value(listed, 900, prefer_ceil=True) == 600

    ranged = FakeProp(2, minimum=50, maximum=1200, step=50)
    assert sb.clamp_property_value(ranged, 203) == 200
    assert sb.clamp_property_value(ranged, 10) == 50
    assert sb.clamp_property_value(ranged, 5000) == 1200

    unknown = FakeProp(3)
    assert sb.clamp_property_value(unknown, 321) == 321


def test_transfer_fallback_order_is_png_bmp_jpeg(tmp_path):
    class Item:
        def __init__(self):
            self.guids = []

        def Transfer(self, guid=None):
            self.guids.append(guid)
            if len(self.guids) < 3:
                raise RuntimeError("format unsupported")
            return FakeImageFile(b"jpeg")

    item = Item()
    path = sb._transfer_to_temp(item, str(tmp_path), sb.ScanOptions(source="flatbed"))
    assert item.guids == [sb.GUID_PNG, sb.GUID_BMP, sb.GUID_JPEG]
    assert Path(path).read_bytes() == b"jpeg"


def test_save_file_failure_falls_back_to_binary_data(tmp_path):
    class Item:
        def Transfer(self, guid=None):
            return BadSaveImageFile(b"binary-data")

    path = sb._transfer_to_temp(Item(), str(tmp_path), sb.ScanOptions(source="flatbed"))
    assert Path(path).read_bytes() == b"binary-data"


def test_missing_device_and_item_properties_do_not_prevent_scan(tmp_path):
    item = FakeItem([])
    info = scanner_info(item, info_props=[], device_props=[], item_props=[], name="Unknown")
    manager = FakeManager([info])
    fake_com = FakeCom()
    options = sb.ScanOptions(dpi=300, color=True, driver="wia", source="flatbed", output_dir=str(tmp_path))

    pages = sb.scan_wia(
        options,
        dispatch=lambda _prog_id: manager,
        com_module=fake_com,
    )

    assert len(pages) == 1
    assert Path(pages[0]).is_file()
    assert [event for event, _tid in fake_com.events] == ["init", "uninit"]


def test_wia_adf_scan_stops_on_paper_empty_and_returns_all_pages(tmp_path):
    item = FakeItem(default_item_props(), fail_after=2)
    info = scanner_info(
        item,
        info_props=[FakeProp(sb.WIA_DIP_DEV_ID, "device-1"), FakeProp(sb.WIA_DIP_DEV_TYPE, 1)],
        device_props=default_device_props(),
        item_props=default_item_props(),
    )
    manager = FakeManager([info])
    fake_com = FakeCom()
    options = sb.ScanOptions(
        dpi=300,
        color=True,
        driver="wia",
        source="adf",
        output_dir=str(tmp_path),
    )

    pages = sb.scan_wia(options, dispatch=lambda _prog_id: manager, com_module=fake_com)

    assert len(pages) == 2
    assert all(Path(path).is_file() for path in pages)
    assert len(item.transfers) == 3


def test_list_devices_exposes_wia_device_without_scanner_hardware(tmp_path):
    info = scanner_info(
        FakeItem(default_item_props()),
        info_props=[
            FakeProp(sb.WIA_DIP_DEV_ID, "wia-device-id"),
            FakeProp(sb.WIA_DIP_DEV_NAME, "Canon MF3010"),
            FakeProp(sb.WIA_DIP_VEND_DESC, "Canon"),
            FakeProp(sb.WIA_DIP_DEV_DESC, "Scanner"),
            FakeProp(sb.WIA_DIP_DEV_TYPE, 1),
        ],
        device_props=default_device_props(),
        item_props=default_item_props(),
    )
    manager = FakeManager([info])

    devices = sb.list_devices(
        "wia",
        dispatch=lambda _prog_id: manager,
        com_module=FakeCom(),
    )

    assert len(devices) == 1
    assert devices[0].backend == "wia"
    assert devices[0].device_id == "wia-device-id"
    assert devices[0].name == "Canon MF3010"
    assert devices[0].key == "wia:wia-device-id"


def test_missing_twain_module_raises_clear_scanner_error():
    class NoTwain:
        pass

    with pytest.raises(sb.ScannerError) as excinfo:
        sb.list_twain_devices(parent_hwnd=1, twain_module=NoTwain())
    assert "TWAIN" in str(excinfo.value)


def test_diagnostics_report_contains_environment_and_discovered_devices(tmp_path):
    info = scanner_info(
        FakeItem(default_item_props()),
        info_props=[
            FakeProp(sb.WIA_DIP_DEV_ID, "device-1"),
            FakeProp(sb.WIA_DIP_DEV_NAME, "Canon MF3010"),
            FakeProp(sb.WIA_DIP_DEV_TYPE, 1),
        ],
        device_props=default_device_props(),
        item_props=default_item_props(),
    )
    manager = FakeManager([info])

    report = sb.build_diagnostics(
        "wia",
        dispatch=lambda _prog_id: manager,
        com_module=FakeCom(),
    )

    assert "تقرير تشخيص الماسح الضوئي" in report
    assert "Canon MF3010" in report
    assert "Python:" in report
    assert "scanner.log" in report
