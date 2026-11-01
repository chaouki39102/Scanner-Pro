# -*- coding: utf-8 -*-
"""Windows scanner backends (WIA and optional TWAIN).

All WIA COM work must happen on a thread initialised as an STA. The UI thread
calls :func:`com_apartment` once, and every scan worker calls it again; nesting
is reference-counted per thread so COM is initialised/uninitialised exactly
once per worker.
"""
from __future__ import annotations

import gc
import importlib.metadata
import logging
import os
import platform
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Iterator, Optional, Sequence

APP_DIR_NAME = "CanonScanPro"


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("canon_scan_pro.scanner")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    log_dir = os.path.join(base, APP_DIR_NAME, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = tempfile.gettempdir()
    try:
        handler = RotatingFileHandler(
            os.path.join(log_dir, "scanner.log"),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"
        ))
        logger.addHandler(handler)
    except Exception:
        pass
    return logger


log = _setup_logging()

WIA_GENERAL_ERROR = 0x80210001
WIA_PAPER_JAM = 0x80210002
WIA_PAPER_EMPTY = 0x80210003
WIA_PAPER_PROBLEM = 0x80210004
WIA_OFFLINE = 0x80210005
WIA_BUSY = 0x80210006
WIA_WARMING_UP = 0x80210007
WIA_USER_INTERVENTION = 0x80210008
WIA_ITEM_DELETED = 0x80210009
WIA_DEVICE_COMMUNICATION = 0x8021000A
WIA_INVALID_COMMAND = 0x8021000B
WIA_INCORRECT_HARDWARE_SETTING = 0x8021000C
WIA_DEVICE_LOCKED = 0x8021000D
WIA_EXCEPTION_IN_DRIVER = 0x8021000E
WIA_INVALID_DRIVER_RESPONSE = 0x8021000F
WIA_NO_DEVICE_AVAILABLE = 0x80210015
WIA_COVER_OPEN = 0x80210016
WIA_LAMP_OFF = 0x80210017
WIA_MULTI_FEED = 0x80210020
WIA_MAXIMUM_PRINTER_ENDORSER_COUNTER = 0x80210021

WIA_ERROR_TEXT = {
    WIA_GENERAL_ERROR: "حدث خطأ غير معروف داخل الماسح الضوئي.",
    WIA_PAPER_JAM: "الورق محشور في درج التغذية. أزل الورق المحشور ثم أعد المحاولة.",
    WIA_PAPER_EMPTY: "درج التغذية فارغ. ضع الورق ثم أعد المحاولة.",
    WIA_PAPER_PROBLEM: "حدثت مشكلة في درج التغذية.",
    WIA_OFFLINE: "الماسح الضوئي غير متصل. تحقق من كابل USB والطاقة.",
    WIA_BUSY: "الماسح الضوئي مشغول الآن. انتظر قليلًا ثم أعد المحاولة.",
    WIA_WARMING_UP: "الماسح الضوئي قيد التسخين. انتظر قليلًا ثم أعد المحاولة.",
    WIA_USER_INTERVENTION: "الماسح الضوئي يحتاج تدخّلًا: تأكد من تشغيله واتصاله بالكمبيوتر.",
    WIA_ITEM_DELETED: "تمت إزالة جهاز الماسح الضوئي. أعد توصيله ثم حدّث قائمة الأجهزة.",
    WIA_DEVICE_COMMUNICATION: "فشل الاتصال بالماسح الضوئي. تحقق من الكابل والطاقة.",
    WIA_INVALID_COMMAND: "الماسح الضوئي لا يدعم هذا الأمر.",
    WIA_INCORRECT_HARDWARE_SETTING: "إعداد غير صحيح على الماسح الضوئي.",
    WIA_DEVICE_LOCKED: "الماسح الضوئي مقفل بواسطة تطبيق آخر. أغلق التطبيقات الأخرى ثم أعد المحاولة.",
    WIA_EXCEPTION_IN_DRIVER: "حدث خطأ داخل برنامج تشغيل الماسح الضوئي.",
    WIA_INVALID_DRIVER_RESPONSE: "استجابة برنامج تشغيل الماسح الضوئي غير صالحة.",
    WIA_NO_DEVICE_AVAILABLE: "لم يعثر Windows على ماسح ضوئي متاح.",
    WIA_COVER_OPEN: "غطاء الماسح الضوئي مفتوح. أغلقه ثم أعد المحاولة.",
    WIA_LAMP_OFF: "مصباح الماسح الضوئي مطفأ.",
    WIA_MULTI_FEED: "التقط الماسح أكثر من ورقة معًا. أعد ترتيب الورق ثم حاول مجددًا.",
    WIA_MAXIMUM_PRINTER_ENDORSER_COUNTER: "تم الوصول إلى الحد الأقصى لعدّاد الطباعة على المستند.",
}

TERMINAL_TRANSFER_ERRORS = {
    WIA_PAPER_JAM,
    WIA_PAPER_EMPTY,
    WIA_PAPER_PROBLEM,
    WIA_OFFLINE,
    WIA_USER_INTERVENTION,
    WIA_ITEM_DELETED,
    WIA_DEVICE_COMMUNICATION,
    WIA_DEVICE_LOCKED,
    WIA_COVER_OPEN,
    WIA_LAMP_OFF,
    WIA_MULTI_FEED,
    WIA_MAXIMUM_PRINTER_ENDORSER_COUNTER,
}

WIA_DATA_THRESHOLD = 0
WIA_DATA_GRAYSCALE = 2
WIA_DATA_COLOR = 3

WIA_INTENT_COLOR = 1
WIA_INTENT_GRAYSCALE = 2
WIA_INTENT_TEXT = 4

WIA_DPS_HORIZONTAL_BED_SIZE = 3074
WIA_DPS_VERTICAL_BED_SIZE = 3075
WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = 3086
WIA_DPS_DOCUMENT_HANDLING_SELECT = 3088
WIA_IPA_DATATYPE = 4103
WIA_IP_INTENT = 6146
WIA_IPA_XRESOLUTION = 6147
WIA_IPA_YRESOLUTION = 6148
WIA_IPA_XPOSITION = 6149
WIA_IPA_YPOSITION = 6150
WIA_IPA_XEXTENT = 6151
WIA_IPA_YEXTENT = 6152
WIA_DIP_DEV_ID = 2
WIA_DIP_VEND_DESC = 3
WIA_DIP_DEV_DESC = 4
WIA_DIP_DEV_TYPE = 5
WIA_DIP_PORT_NAME = 6
WIA_DIP_DEV_NAME = 7

WIA_DEVICE_TYPE_SCANNER = 1
WIA_FEEDER = 1
WIA_FLATBED = 2
WIA_DUPLEX = 4

GUID_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
GUID_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
GUID_JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"

WIA_BIAS_MAXIMIZE_QUALITY = 131072

_TRANSFER_FORMATS = (
    ("PNG", GUID_PNG, ".png"),
    ("BMP", GUID_BMP, ".bmp"),
    ("JPEG", GUID_JPEG, ".jpg"),
    ("driver", None, ".jpg"),
)

_COM_STATE = threading.local()
_LAST_ERROR_LOCK = threading.Lock()
_LAST_ERROR: Optional["ScannerError"] = None


class ScannerError(RuntimeError):
    def __init__(
        self,
        message: str,
        hresult: Optional[int] = None,
        *,
        cancelled: bool = False,
        pages: Optional[Sequence[str]] = None,
    ):
        super().__init__(message)
        self.hresult = hresult
        self.cancelled = cancelled
        self.pages = list(pages or [])


@dataclass
class ScannerDevice:
    backend: str
    device_id: str
    name: str
    description: str = ""
    manufacturer: str = ""
    device_type: int = 0
    port: str = ""

    @property
    def key(self) -> str:
        return f"{self.backend}:{self.device_id or self.name}"

    @property
    def display_name(self) -> str:
        label = self.name or self.description or self.device_id or "ماسح ضوئي"
        backend = self.backend.upper()
        if self.manufacturer and self.manufacturer.lower() not in label.lower():
            return f"{label} — {self.manufacturer} ({backend})"
        return f"{label} ({backend})"


@dataclass
class ScanOptions:
    dpi: int = 300
    color: bool = True
    driver: str = "auto"
    device_key: str = ""
    source: str = "flatbed"
    page_limit: int = 0
    output_dir: Optional[str] = None
    parent_hwnd: int = 0

    def normalised(self) -> "ScanOptions":
        driver = (self.driver or "auto").strip().lower()
        if driver not in ("auto", "wia", "twain"):
            driver = "auto"
        source = (self.source or "flatbed").strip().lower()
        if source not in ("flatbed", "adf", "duplex"):
            source = "flatbed"
        try:
            dpi = int(self.dpi)
        except Exception:
            dpi = 300
        dpi = max(50, min(2400, dpi))
        try:
            limit = int(self.page_limit)
        except Exception:
            limit = 0
        return ScanOptions(
            dpi=dpi,
            color=bool(self.color),
            driver=driver,
            device_key=self.device_key or "",
            source=source,
            page_limit=max(0, limit),
            output_dir=self.output_dir,
            parent_hwnd=int(self.parent_hwnd or 0),
        )


def _record_error(exc: ScannerError) -> ScannerError:
    global _LAST_ERROR
    with _LAST_ERROR_LOCK:
        _LAST_ERROR = exc
    return exc


def get_last_error() -> Optional[ScannerError]:
    with _LAST_ERROR_LOCK:
        return _LAST_ERROR


def hresult_from_exception(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "hresult", None)
    if code is None:
        args = getattr(exc, "args", ())
        if args:
            code = args[0]
    if isinstance(code, int):
        return code & 0xFFFFFFFF
    return None


def describe_hresult(hresult: Optional[int]) -> str:
    if hresult is None:
        return ""
    text = WIA_ERROR_TEXT.get(hresult)
    if text:
        return f"{text} (0x{hresult:08X})"
    return f"رمز خطأ Windows: 0x{hresult:08X}"


def _error_from_exception(
    exc: BaseException,
    context: str,
    *,
    pages: Optional[Sequence[str]] = None,
) -> ScannerError:
    if isinstance(exc, ScannerError):
        if pages and not exc.pages:
            exc.pages = list(pages)
        return _record_error(exc)
    hresult = hresult_from_exception(exc)
    detail = describe_hresult(hresult) or str(exc) or exc.__class__.__name__
    log.error("%s: %r", context, exc)
    return _record_error(ScannerError(f"{context}\n{detail}", hresult, pages=pages))


def _import_com():
    try:
        import pythoncom
        return pythoncom
    except Exception as exc:
        raise _record_error(ScannerError(
            "مكتبة pywin32 غير مثبتة أو تالفة. شغّل install.bat ثم أعد فتح البرنامج.",
            hresult_from_exception(exc),
        ))


@contextmanager
def com_apartment(com_module: Any = None):
    """Initialise COM once per worker thread, including nested usage."""
    com = com_module or _import_com()
    depth = int(getattr(_COM_STATE, "depth", 0))
    initialised_here = False
    if depth == 0:
        try:
            com.CoInitialize()
        except Exception as exc:
            raise _error_from_exception(exc, "تعذر تهيئة COM على خيط العمل.")
        initialised_here = True
        log.debug("CoInitialize on thread %s", threading.get_ident())
    _COM_STATE.depth = depth + 1
    try:
        yield
    finally:
        depth = int(getattr(_COM_STATE, "depth", 1)) - 1
        _COM_STATE.depth = max(0, depth)
        if depth == 0 and initialised_here:
            try:
                gc.collect()
                com.CoUninitialize()
                log.debug("CoUninitialize on thread %s", threading.get_ident())
            except Exception:
                log.exception("CoUninitialize failed")


def _com_call(
    func: Callable[[], Any],
    *,
    com_module: Any = None,
    context: str = "تعذر تنفيذ عملية WIA.",
) -> Any:
    """Run a callback in a COM apartment and never leak COM refs past CoUninitialize."""
    failure = None
    result = None
    with com_apartment(com_module):
        try:
            result = func()
        except ScannerError as exc:
            failure = (str(exc), exc.hresult, exc.cancelled, list(exc.pages))
        except Exception as exc:
            err = _error_from_exception(exc, context)
            failure = (str(err), err.hresult, err.cancelled, list(err.pages))
    if failure is not None:
        message, hresult, cancelled, pages = failure
        raise _record_error(ScannerError(
            message,
            hresult,
            cancelled=cancelled,
            pages=pages,
        ))
    return result


def _iter_collection(collection: Any) -> Iterator[Any]:
    if collection is None:
        return
    if isinstance(collection, (list, tuple)):
        yield from collection
        return
    try:
        iterator = iter(collection)
    except TypeError:
        iterator = None
    if iterator is not None:
        while True:
            try:
                item = next(iterator)
            except StopIteration:
                return
            except Exception:
                return
            yield item
        return
    try:
        count = int(collection.Count)
    except Exception:
        return
    for index in range(1, count + 1):
        try:
            yield collection.Item(index)
        except Exception:
            return


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _property_id(prop: Any) -> Optional[int]:
    value = _safe_attr(prop, "PropertyID")
    try:
        return int(value)
    except Exception:
        return None


def find_property(properties: Any, property_id: int) -> Any:
    if properties is None:
        return None
    for prop in _iter_collection(properties):
        if _property_id(prop) == property_id:
            return prop
    try:
        prop = properties.Item(property_id)
        if prop is not None:
            return prop
    except Exception:
        pass
    return None


def read_property(properties: Any, property_id: int, default: Any = None) -> Any:
    prop = find_property(properties, property_id)
    if prop is None:
        return default
    value = _safe_attr(prop, "Value", default)
    return default if value is None else value


def _as_value_list(values: Any) -> list[Any]:
    if values is None:
        return []
    if isinstance(values, (list, tuple)):
        return list(values)
    try:
        return list(values)
    except Exception:
        return [values]


def _numeric_values(prop: Any) -> list[float]:
    raw = _as_value_list(_safe_attr(prop, "SubTypeValues"))
    result = []
    for value in raw:
        try:
            result.append(float(value))
        except Exception:
            continue
    return result


def _prop_min(prop: Any) -> Optional[float]:
    for name in ("SubTypeMin", "Min"):
        value = _safe_attr(prop, name)
        try:
            if value is not None:
                return float(value)
        except Exception:
            continue
    return None


def _prop_max(prop: Any) -> Optional[float]:
    for name in ("SubTypeMax", "Max"):
        value = _safe_attr(prop, name)
        try:
            if value is not None:
                return float(value)
        except Exception:
            continue
    return None


def _prop_step(prop: Any) -> Optional[float]:
    for name in ("SubTypeStep", "Step"):
        value = _safe_attr(prop, name)
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except Exception:
            continue
    return None


def clamp_property_value(prop: Any, value: int, *, prefer_ceil: bool = False) -> int:
    """Clamp an integer to the driver's documented range/list, safely."""
    want = int(value)
    numeric = _numeric_values(prop)
    if numeric:
        if want in {int(v) for v in numeric}:
            return want
        if prefer_ceil:
            higher = [v for v in numeric if v >= want]
            chosen = min(higher) if higher else max(numeric)
        else:
            chosen = min(numeric, key=lambda v: (abs(v - want), v))
        return int(round(chosen))
    minimum = _prop_min(prop)
    maximum = _prop_max(prop)
    if minimum is not None and want < minimum:
        want = int(round(minimum))
    if maximum is not None and want > maximum:
        want = int(round(maximum))
    step = _prop_step(prop)
    if step and step > 0:
        base = minimum if minimum is not None else 0.0
        want = int(round(base + round((want - base) / step) * step))
        if minimum is not None:
            want = max(want, int(round(minimum)))
        if maximum is not None:
            want = min(want, int(round(maximum)))
    return int(want)


def _is_read_only(prop: Any) -> bool:
    for name in ("IsReadOnly", "ReadOnly"):
        value = _safe_attr(prop, name)
        if isinstance(value, bool) and value:
            return True
    return False


def _set_numeric_property(
    properties: Any,
    property_id: int,
    value: int,
    *,
    label: str,
    prefer_ceil: bool = False,
) -> bool:
    prop = find_property(properties, property_id)
    if prop is None:
        log.info("%s (%s) is not exposed by this driver; leaving default.", label, property_id)
        return False
    if _is_read_only(prop):
        log.info("%s (%s) is read-only; leaving default.", label, property_id)
        return False
    clamped = clamp_property_value(prop, value, prefer_ceil=prefer_ceil)
    if clamped != int(value):
        log.info("%s clamped from %s to %s (driver limits).", label, value, clamped)
    try:
        prop.Value = clamped
        return True
    except Exception as exc:
        log.warning("Could not set %s=%s: %r", label, clamped, exc)
        return False


def _set_value(prop: Any, value: Any, label: str) -> bool:
    if _is_read_only(prop):
        log.info("%s is read-only.", label)
        return False
    try:
        prop.Value = value
        return True
    except Exception as exc:
        log.warning("Could not set %s=%r: %r", label, value, exc)
        return False


def _flag_values(prop: Any) -> Optional[set[int]]:
    raw = _as_value_list(_safe_attr(prop, "SubTypeValues"))
    values = set()
    for value in raw:
        try:
            values.add(int(value))
        except Exception:
            continue
    return values or None


def _select_source(device: Any, options: ScanOptions) -> None:
    properties = _safe_attr(device, "Properties")
    prop = find_property(properties, WIA_DPS_DOCUMENT_HANDLING_SELECT)
    if prop is None:
        if options.source != "flatbed":
            raise _record_error(ScannerError(
                "برنامج تشغيل الماسح لا يوفّر إعداد مصدر الورق (زجاج/درج تغذية)، "
                "لذلك لا يمكن اختيار درج التغذية أو الوجهين."
            ))
        return
    supported = _flag_values(prop)
    if options.source == "adf":
        required = WIA_FEEDER
        wanted = WIA_FEEDER
        label = "درج التغذية (ADF)"
    elif options.source == "duplex":
        required = WIA_FEEDER | WIA_DUPLEX
        wanted = WIA_FEEDER | WIA_DUPLEX
        label = "درج التغذية مع الوجهين"
    else:
        required = None
        wanted = WIA_FLATBED
        label = "زجاج المسح"
    if supported is not None and required is not None:
        if options.source == "duplex":
            ok = wanted in supported or (WIA_FEEDER in supported and WIA_DUPLEX in supported)
        else:
            ok = required in supported or wanted in supported
        if not ok:
            raise _record_error(ScannerError(f"الماسح الضوئي لا يدعم {label}."))
    if _set_value(prop, wanted, "مصدر الورق"):
        log.info("Scanner source set to %s (value %s).", options.source, wanted)


def _bed_size(device: Any) -> tuple[Optional[float], Optional[float]]:
    properties = _safe_attr(device, "Properties")
    width = read_property(properties, WIA_DPS_HORIZONTAL_BED_SIZE)
    height = read_property(properties, WIA_DPS_VERTICAL_BED_SIZE)
    try:
        width = float(width)
        if width <= 0:
            width = None
    except Exception:
        width = None
    try:
        height = float(height)
        if height <= 0:
            height = None
    except Exception:
        height = None
    return width, height


def _max_extent(properties: Any, property_id: int) -> Optional[float]:
    prop = find_property(properties, property_id)
    if prop is None:
        return None
    value = _prop_max(prop)
    if value is not None and value > 0:
        return value
    return None


def _choose_datatype(properties: Any, color: bool) -> int:
    if color:
        wanted = (WIA_DATA_COLOR, WIA_DATA_GRAYSCALE, WIA_DATA_THRESHOLD)
    else:
        wanted = (WIA_DATA_GRAYSCALE, WIA_DATA_THRESHOLD)
    prop = find_property(properties, WIA_IPA_DATATYPE)
    if prop is None:
        log.info("WIA_IPA_DATATYPE is unavailable; leaving the driver default.")
        return wanted[0]
    supported = set()
    for value in _as_value_list(_safe_attr(prop, "SubTypeValues")):
        try:
            supported.add(int(value))
        except Exception:
            continue
    for value in wanted:
        if not supported or value in supported:
            return value
    if supported:
        return min(supported)
    return wanted[0]


def _configure_item(item: Any, device: Any, options: ScanOptions) -> None:
    properties = _safe_attr(item, "Properties")
    if properties is None:
        raise _record_error(ScannerError("تعذر الوصول إلى إعدادات عنصر المسح في برنامج التشغيل."))

    intent = WIA_INTENT_COLOR if options.color else WIA_INTENT_GRAYSCALE
    intent_prop = find_property(properties, WIA_IP_INTENT)
    if intent_prop is not None:
        _set_value(intent_prop, intent, "نوع الصورة (Intent)")

    datatype = _choose_datatype(properties, options.color)
    datatype_prop = find_property(properties, WIA_IPA_DATATYPE)
    if datatype_prop is not None:
        _set_value(datatype_prop, datatype, "نوع بيانات الصورة")

    _select_source(device, options)

    _set_numeric_property(
        properties,
        WIA_IPA_XRESOLUTION,
        options.dpi,
        label="الدقة الأفقية",
        prefer_ceil=True,
    )
    _set_numeric_property(
        properties,
        WIA_IPA_YRESOLUTION,
        options.dpi,
        label="الدقة العمودية",
        prefer_ceil=True,
    )

    _set_numeric_property(properties, WIA_IPA_XPOSITION, 0, label="بداية المسح الأفقية")
    _set_numeric_property(properties, WIA_IPA_YPOSITION, 0, label="بداية المسح العمودية")

    bed_w, bed_h = _bed_size(device)
    width = int(round(bed_w / 1000.0 * options.dpi)) if bed_w else None
    height = int(round(bed_h / 1000.0 * options.dpi)) if bed_h else None
    if not width:
        width = _max_extent(properties, WIA_IPA_XEXTENT)
        width = int(round(width)) if width else int(round(8.267 * options.dpi))
    if not height:
        height = _max_extent(properties, WIA_IPA_YEXTENT)
        height = int(round(height)) if height else int(round(11.692 * options.dpi))

    _set_numeric_property(properties, WIA_IPA_XEXTENT, width, label="عرض منطقة المسح")
    _set_numeric_property(properties, WIA_IPA_YEXTENT, height, label="ارتفاع منطقة المسح")


def _temp_directory(options: ScanOptions) -> str:
    directory = options.output_dir or tempfile.gettempdir()
    os.makedirs(directory, exist_ok=True)
    return directory


def _temp_path(directory: str, extension: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(directory, f"canon_scan_{stamp}_{uuid.uuid4().hex[:12]}{extension}")


def _remove_quietly(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def _save_image_file(image_file: Any, path: str) -> None:
    try:
        image_file.SaveFile(path)
    except Exception as exc:
        log.warning("ImageFile.SaveFile failed (%r); trying BinaryData.", exc)
        _remove_quietly(path)
        data = None
        try:
            data = image_file.FileData.BinaryData
        except Exception as exc2:
            log.error("ImageFile.FileData.BinaryData failed: %r", exc2)
        if not data:
            raise
        with open(path, "wb") as stream:
            stream.write(data)
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        _remove_quietly(path)
        raise ScannerError("تعذر حفظ الصورة التي أعادها الماسح الضوئي.")


def _common_dialog_acquire(options: ScanOptions, directory: str) -> str:
    try:
        import win32com.client
    except Exception as exc:
        raise _error_from_exception(exc, "تعذر تحميل win32com لفتح نافذة المسح البديلة.")
    intent = WIA_INTENT_COLOR if options.color else WIA_INTENT_GRAYSCALE
    try:
        dialog = win32com.client.Dispatch("WIA.CommonDialog")
        image_file = dialog.ShowAcquireImage(
            WIA_DEVICE_TYPE_SCANNER,
            intent,
            WIA_BIAS_MAXIMIZE_QUALITY,
            "",
            False,
            True,
            True,
        )
    except Exception as exc:
        raise _error_from_exception(exc, "فشلت نافذة المسح البديلة الخاصة بـ Windows.")
    if image_file is None:
        raise _record_error(ScannerError("تم إلغاء المسح.", cancelled=True))
    path = _temp_path(directory, ".jpg")
    try:
        _save_image_file(image_file, path)
    except Exception as exc:
        _remove_quietly(path)
        raise _error_from_exception(exc, "تعذر حفظ الصورة من نافذة المسح البديلة.")
    log.info("Acquired image through WIA.CommonDialog.")
    return path


def _transfer_to_temp(item: Any, directory: str, options: ScanOptions) -> str:
    last_error: Optional[BaseException] = None
    for label, guid, extension in _TRANSFER_FORMATS:
        path = _temp_path(directory, extension)
        try:
            image_file = item.Transfer(guid) if guid else item.Transfer()
            if image_file is None:
                raise ScannerError(f"لم يُعد الماسح صورة بصيغة {label}.")
            _save_image_file(image_file, path)
            log.info("Transferred page using %s.", label)
            return path
        except Exception as exc:
            _remove_quietly(path)
            last_error = exc
            hresult = hresult_from_exception(exc)
            if hresult in TERMINAL_TRANSFER_ERRORS:
                raise _error_from_exception(exc, "تعذر سحب الصورة من الماسح الضوئي.")
            log.warning("Transfer format %s failed: %r", label, exc)
    if options.source != "flatbed":
        raise _error_from_exception(
            last_error or ScannerError("فشل سحب الصورة من درج التغذية."),
            "فشل سحب الصورة من الماسح الضوئي بكل الصيغ المتاحة.",
        )
    log.error("All WIA transfer formats failed; trying CommonDialog. Last error: %r", last_error)
    try:
        return _common_dialog_acquire(options, directory)
    except ScannerError:
        raise
    except Exception as exc:
        raise _error_from_exception(exc, "فشل سحب الصورة من الماسح الضوئي بكل الصيغ المتاحة.")


def _device_info_value(info: Any, property_ids: Sequence[int], names: Sequence[str], default: str = "") -> str:
    properties = _safe_attr(info, "Properties")
    for property_id in property_ids:
        value = read_property(properties, property_id)
        if value not in (None, ""):
            return str(value).strip()
    for name in names:
        try:
            value = properties.Item(name).Value
            if value not in (None, ""):
                return str(value).strip()
        except Exception:
            continue
        value = _safe_attr(info, name)
        if value not in (None, ""):
            return str(value).strip()
    return default


def _scanner_device_from_info(info: Any) -> ScannerDevice:
    device_id = _device_info_value(
        info,
        (WIA_DIP_DEV_ID,),
        ("DeviceID", "Id", "Name"),
        default="",
    )
    name = _device_info_value(
        info,
        (WIA_DIP_DEV_NAME, WIA_DIP_DEV_DESC),
        ("Name", "DeviceName"),
        default=device_id or "Scanner",
    )
    description = _device_info_value(
        info,
        (WIA_DIP_DEV_DESC,),
        ("Description",),
        default="",
    )
    manufacturer = _device_info_value(
        info,
        (WIA_DIP_VEND_DESC,),
        ("Manufacturer",),
        default="",
    )
    port = _device_info_value(
        info,
        (WIA_DIP_PORT_NAME,),
        ("Port",),
        default="",
    )
    try:
        device_type = int(read_property(_safe_attr(info, "Properties"), WIA_DIP_DEV_TYPE, 0) or 0)
    except Exception:
        device_type = 0
    return ScannerDevice(
        backend="wia",
        device_id=device_id or name,
        name=name,
        description=description,
        manufacturer=manufacturer,
        device_type=device_type,
        port=port,
    )


def _device_rank(device: ScannerDevice) -> tuple:
    text = f"{device.name} {device.description} {device.manufacturer}".lower()
    return (
        0 if device.device_type == WIA_DEVICE_TYPE_SCANNER else 1,
        0 if "canon" in text else 1,
        0 if "mf3010" in text else 1,
        device.name.lower(),
    )


def _dispatch_wia(dispatch: Optional[Callable[..., Any]] = None) -> Any:
    if dispatch is not None:
        return dispatch("WIA.DeviceManager")
    try:
        import win32com.client
    except Exception as exc:
        raise _error_from_exception(exc, "مكتبة pywin32 غير مثبتة. شغّل install.bat ثم أعد فتح البرنامج.")
    try:
        return win32com.client.Dispatch("WIA.DeviceManager")
    except Exception as exc:
        raise _error_from_exception(
            exc,
            "تعذر تشغيل خدمة WIA في Windows. تأكد من تثبيت برنامج تشغيل الماسح الضوئي.",
        )


def _list_wia_inner(dispatch: Optional[Callable[..., Any]]) -> list[ScannerDevice]:
    manager = None
    infos = None
    info = None
    devices: list[ScannerDevice] = []
    try:
        manager = _dispatch_wia(dispatch)
        infos = list(_iter_collection(_safe_attr(manager, "DeviceInfos")))
        for info in infos:
            devices.append(_scanner_device_from_info(info))
        return devices
    finally:
        manager = None
        infos = None
        info = None


def list_wia_devices(
    dispatch: Optional[Callable[..., Any]] = None,
    com_module: Any = None,
) -> list[ScannerDevice]:
    if not sys_platform_windows():
        raise _record_error(ScannerError("المسح الضوئي عبر WIA متاح على Windows فقط."))
    devices = _com_call(
        lambda: _list_wia_inner(dispatch),
        com_module=com_module,
        context="تعذر قراءة قائمة أجهزة WIA.",
    )
    devices.sort(key=_device_rank)
    log.info("WIA devices: %s", [d.display_name for d in devices])
    return devices


def _import_twain(twain_module: Any = None):
    if twain_module is not None:
        return twain_module
    try:
        import twain
        return twain
    except Exception as exc:
        raise _error_from_exception(
            exc,
            "مكوّن TWAIN غير مثبت. ثبّته عبر: pip install pytwain",
        )


def _twain_parent_window(parent_hwnd: int) -> int:
    try:
        hwnd = int(parent_hwnd or 0)
    except Exception:
        hwnd = 0
    if hwnd <= 0:
        raise _record_error(ScannerError(
            "المسح عبر TWAIN يحتاج نافذة التطبيق. أعد فتح البرنامج ثم حاول مجددًا."
        ))
    return hwnd


def list_twain_devices(
    parent_hwnd: int = 0,
    twain_module: Any = None,
) -> list[ScannerDevice]:
    if not sys_platform_windows():
        raise _record_error(ScannerError("المسح الضوئي عبر TWAIN متاح على Windows فقط."))
    twain = _import_twain(twain_module)
    hwnd = _twain_parent_window(parent_hwnd)
    manager = None
    try:
        manager = twain.SourceManager(parent_window=hwnd)
        names = _as_value_list(_safe_attr(manager, "source_list"))
        devices = [
            ScannerDevice(
                backend="twain",
                device_id=str(name),
                name=str(name),
                description="TWAIN",
            )
            for name in names
            if str(name).strip()
        ]
        log.info("TWAIN devices: %s", [d.display_name for d in devices])
        return devices
    except ScannerError:
        raise
    except Exception as exc:
        raise _error_from_exception(exc, "تعذر قراءة قائمة أجهزة TWAIN.")
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass


def sys_platform_windows() -> bool:
    return os.name == "nt"


def _no_devices_error(errors: Sequence[str]) -> ScannerError:
    message = (
        "لم يعثر Windows على ماسح ضوئي جاهز.\n"
        "تأكد من تشغيل الماسح وتوصيله، ومن تثبيت برنامج التشغيل الصحيح، ثم اضغط «تحديث».\n"
        "إن كان الجهاز يعمل في تطبيق Windows Scan فاستخدم زر «تشخيص الماسح» لمعرفة السبب."
    )
    if errors:
        message += "\n" + "\n".join(errors)
    return _record_error(ScannerError(message, WIA_NO_DEVICE_AVAILABLE))


def list_devices(
    driver: str = "auto",
    *,
    parent_hwnd: int = 0,
    dispatch: Optional[Callable[..., Any]] = None,
    com_module: Any = None,
    twain_module: Any = None,
) -> list[ScannerDevice]:
    driver = (driver or "auto").strip().lower()
    if driver not in ("auto", "wia", "twain"):
        driver = "auto"
    devices: list[ScannerDevice] = []
    errors: list[str] = []
    if driver in ("auto", "wia"):
        try:
            devices.extend(list_wia_devices(dispatch=dispatch, com_module=com_module))
        except ScannerError as exc:
            log.warning("WIA discovery failed: %s", exc)
            if driver == "wia":
                raise
            errors.append(f"WIA: {exc}")
    if driver in ("auto", "twain"):
        try:
            devices.extend(list_twain_devices(parent_hwnd=parent_hwnd, twain_module=twain_module))
        except ScannerError as exc:
            log.info("TWAIN discovery unavailable: %s", exc)
            if driver == "twain":
                raise
            errors.append(f"TWAIN: {exc}")
    if not devices:
        raise _no_devices_error(errors)
    devices.sort(key=_device_rank)
    return devices


def _select_wia_info(infos: Sequence[Any], device_key: str) -> Any:
    wanted = (device_key or "").strip()
    if wanted.startswith("wia:"):
        wanted = wanted[4:]
    candidates = []
    for info in infos:
        device = _scanner_device_from_info(info)
        candidates.append((info, device))
    if wanted:
        for info, device in candidates:
            if wanted in (device.device_id, device.name, device.key):
                return info
        log.warning("Saved WIA device %r no longer present.", wanted)
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda pair: _device_rank(pair[1]))
    return candidates[0][0]


def _connect_wia(info: Any) -> Any:
    try:
        return info.Connect()
    except Exception as exc:
        raise _error_from_exception(exc, "تعذر الاتصال بالماسح الضوئي المحدد.")


def _first_item(device: Any) -> Any:
    try:
        items = device.Items
        for item in _iter_collection(items):
            return item
        return items(1)
    except Exception:
        pass
    try:
        return device.Items(1)
    except Exception as exc:
        raise _error_from_exception(exc, "تعذر الوصول إلى عنصر المسح في الماسح الضوئي.")


def _acquire_wia_pages(item: Any, options: ScanOptions, directory: str) -> list[str]:
    pages: list[str] = []
    adf = options.source in ("adf", "duplex")
    limit = options.page_limit if options.page_limit > 0 else (200 if adf else 1)
    while len(pages) < limit:
        try:
            path = _transfer_to_temp(item, directory, options)
        except ScannerError as exc:
            if adf and exc.hresult == WIA_PAPER_EMPTY:
                if pages:
                    break
                raise _record_error(ScannerError(
                    WIA_ERROR_TEXT[WIA_PAPER_EMPTY],
                    exc.hresult,
                ))
            if pages:
                exc.pages = pages
            raise
        pages.append(path)
        if not adf:
            break
    if not pages:
        raise _record_error(ScannerError("لم يُعد الماسح الضوئي أي صورة."))
    return pages


def _scan_wia_inner(
    options: ScanOptions,
    directory: str,
    dispatch: Optional[Callable[..., Any]],
) -> list[str]:
    manager = None
    infos = None
    info = None
    device = None
    item = None
    try:
        manager = _dispatch_wia(dispatch)
        infos = list(_iter_collection(_safe_attr(manager, "DeviceInfos")))
        info = _select_wia_info(infos, options.device_key)
        if info is None:
            if not infos:
                raise _record_error(ScannerError(
                    "لم يعثر Windows على ماسح ضوئي عبر WIA. ثبّت برنامج التشغيل وأعد تشغيل الجهاز.",
                    WIA_NO_DEVICE_AVAILABLE,
                ))
            raise _record_error(ScannerError(
                "الماسح الضوئي المحدد غير متاح الآن. اضغط «تحديث» واختر جهازًا آخر.",
                WIA_NO_DEVICE_AVAILABLE,
            ))
        device = _connect_wia(info)
        item = _first_item(device)
        _configure_item(item, device, options)
        pages = _acquire_wia_pages(item, options, directory)
        log.info("WIA scan finished: %s page(s).", len(pages))
        return pages
    finally:
        manager = None
        infos = None
        info = None
        device = None
        item = None


def scan_wia(
    options: ScanOptions,
    *,
    dispatch: Optional[Callable[..., Any]] = None,
    com_module: Any = None,
) -> list[str]:
    options = options.normalised()
    directory = _temp_directory(options)
    return _com_call(
        lambda: _scan_wia_inner(options, directory, dispatch),
        com_module=com_module,
        context="تعذر تنفيذ المسح عبر WIA.",
    )


def scan_twain(
    options: ScanOptions,
    *,
    twain_module: Any = None,
) -> list[str]:
    options = options.normalised()
    directory = _temp_directory(options)
    twain = _import_twain(twain_module)
    hwnd = _twain_parent_window(options.parent_hwnd)
    manager = None
    source = None
    pages: list[str] = []
    limit = options.page_limit if options.page_limit > 0 else (200 if options.source in ("adf", "duplex") else 1)

    def before(settings: dict) -> str:
        if len(pages) >= limit:
            exceptions = getattr(twain, "exceptions", None)
            cancel = getattr(exceptions, "CancelAll", None)
            if cancel is not None:
                raise cancel()
            raise RuntimeError("page limit reached")
        path = _temp_path(directory, ".png")
        pages.append(path)
        return path

    try:
        manager = twain.SourceManager(parent_window=hwnd)
        names = [str(n) for n in _as_value_list(_safe_attr(manager, "source_list"))]
        wanted = options.device_key[6:] if options.device_key.startswith("twain:") else options.device_key
        if wanted and wanted not in names:
            raise _record_error(ScannerError(
                "ماسح TWAIN المحدد غير متاح الآن. اضغط «تحديث» واختر جهازًا آخر."
            ))
        source = manager.open_source(wanted or None)
        if source is None:
            raise _record_error(ScannerError(
                "لم يتم اختيار ماسح TWAIN. أعد المحاولة واختر الجهاز من نافذة الماسح.",
                cancelled=True,
            ))
        _apply_twain_settings(source, twain, options)
        source.acquire_file(before=before, show_ui=True, modal=False)
        valid_pages = [p for p in pages if os.path.isfile(p) and os.path.getsize(p) > 0]
        if not valid_pages:
            raise _record_error(ScannerError("لم يُعد ماسح TWAIN أي صورة.", cancelled=True))
        log.info("TWAIN scan finished: %s page(s).", len(valid_pages))
        return valid_pages
    except ScannerError:
        raise
    except Exception as exc:
        if exc.__class__.__name__ in ("CancelAll", "TwainCancel"):
            raise _record_error(ScannerError("تم إلغاء المسح.", cancelled=True))
        valid_pages = [p for p in pages if os.path.isfile(p) and os.path.getsize(p) > 0]
        raise _error_from_exception(exc, "تعذر تنفيذ المسح عبر TWAIN.", pages=valid_pages)
    finally:
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass


def _apply_twain_settings(source: Any, twain: Any, options: ScanOptions) -> None:
    constants = getattr(twain, "constants", None)
    if constants is None:
        return

    def constant(name: str, default: Any = None) -> Any:
        return getattr(constants, name, default)

    def set_cap(name: str, value: Any, cap_type: str) -> None:
        cap = constant(name)
        if cap is None:
            return
        try:
            source.set_capability(cap, cap_type, value)
        except Exception as exc:
            log.info("TWAIN capability %s=%r not applied: %r", name, value, exc)

    fix32 = constant("TWTY_FIX32")
    uint16 = constant("TWTY_UINT16")
    bool_type = constant("TWTY_BOOL")
    if fix32 is not None:
        set_cap("ICAP_XRESOLUTION", float(options.dpi), fix32)
        set_cap("ICAP_YRESOLUTION", float(options.dpi), fix32)
    if uint16 is not None:
        pixel = constant("TWPT_RGB" if options.color else "TWPT_GRAY")
        if pixel is not None:
            set_cap("ICAP_PIXELTYPE", pixel, uint16)
    if options.source in ("adf", "duplex") and bool_type is not None:
        set_cap("CAP_FEEDERENABLED", True, bool_type)
        set_cap("CAP_AUTOFEED", True, bool_type)
    if options.source == "duplex" and bool_type is not None:
        set_cap("CAP_DUPLEXENABLED", True, bool_type)


def scan(
    options: ScanOptions,
    *,
    dispatch: Optional[Callable[..., Any]] = None,
    com_module: Any = None,
    twain_module: Any = None,
) -> list[str]:
    options = options.normalised()
    if options.driver == "wia":
        return scan_wia(options, dispatch=dispatch, com_module=com_module)
    if options.driver == "twain":
        return scan_twain(options, twain_module=twain_module)
    try:
        return scan_wia(options, dispatch=dispatch, com_module=com_module)
    except ScannerError as wia_error:
        if wia_error.cancelled or wia_error.pages:
            raise
        log.warning("WIA scan failed; trying TWAIN. %s", wia_error)
        try:
            return scan_twain(options, twain_module=twain_module)
        except ScannerError as twain_error:
            if twain_error.cancelled:
                raise
            raise _record_error(ScannerError(
                "تعذر المسح عبر WIA وTWAIN.\n"
                f"WIA: {wia_error}\n"
                f"TWAIN: {twain_error}",
                wia_error.hresult or twain_error.hresult,
            ))


def _pywin32_version() -> str:
    try:
        return importlib.metadata.version("pywin32")
    except Exception:
        return "غير معروف"


def _wia_registered() -> bool:
    if not sys_platform_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "WIA.DeviceManager"):
            return True
    except Exception:
        return False


def _twain_dsm_present() -> bool:
    if not sys_platform_windows():
        return False
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        os.path.join(windir, "System32", "twaindsm.dll"),
        os.path.join(windir, "SysWOW64", "twaindsm.dll"),
    ]
    return any(os.path.isfile(path) for path in candidates)


def _wia_info_details(info: Any) -> list[str]:
    properties = _safe_attr(info, "Properties")
    lines = []
    for property_id, label in (
        (WIA_DIP_DEV_ID, "المعرّف"),
        (WIA_DIP_DEV_NAME, "الاسم"),
        (WIA_DIP_VEND_DESC, "الشركة"),
        (WIA_DIP_DEV_DESC, "الوصف"),
        (WIA_DIP_PORT_NAME, "المنفذ"),
        (WIA_DIP_DEV_TYPE, "النوع"),
        (WIA_DPS_HORIZONTAL_BED_SIZE, "عرض السرير"),
        (WIA_DPS_VERTICAL_BED_SIZE, "ارتفاع السرير"),
        (WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES, "قدرات مصدر الورق"),
    ):
        value = read_property(properties, property_id)
        if value not in (None, ""):
            lines.append(f"    {label}: {value}")
    return lines


def _wia_diagnostic_lines(
    dispatch: Optional[Callable[..., Any]],
) -> tuple[list[str], int]:
    manager = None
    infos = None
    info = None
    lines: list[str] = []
    found = 0
    try:
        manager = _dispatch_wia(dispatch)
        infos = list(_iter_collection(_safe_attr(manager, "DeviceInfos")))
        for info in infos:
            found += 1
            device = _scanner_device_from_info(info)
            lines.append(f"  - WIA: {device.display_name}")
            lines.append(f"    المعرف: {device.device_id}")
            lines.extend(_wia_info_details(info))
        return lines, found
    finally:
        manager = None
        infos = None
        info = None


def build_diagnostics(
    driver: str = "auto",
    *,
    parent_hwnd: int = 0,
    dispatch: Optional[Callable[..., Any]] = None,
    com_module: Any = None,
    twain_module: Any = None,
) -> str:
    lines = [
        "تقرير تشخيص الماسح الضوئي — Canon Scan Pro",
        f"التاريخ: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"نظام التشغيل: {platform.platform()}",
        f"Python: {platform.python_version()} ({platform.architecture()[0]})",
        f"pywin32: {_pywin32_version()}",
        f"تسجيل WIA.DeviceManager: {'نعم' if _wia_registered() else 'غير مؤكد/غير موجود'}",
        f"ملف TWAIN DSM: {'موجود' if _twain_dsm_present() else 'غير موجود'}",
        "",
        "الأجهزة المكتشفة:",
    ]
    found = 0
    if driver in ("auto", "wia"):
        try:
            wia_lines, wia_found = _com_call(
                lambda: _wia_diagnostic_lines(dispatch),
                com_module=com_module,
                context="تعذر فحص أجهزة WIA.",
            )
            lines.extend(wia_lines)
            found += wia_found
        except Exception as exc:
            lines.append(f"  - WIA: فشل الفحص — {exc}")
    if driver in ("auto", "twain"):
        try:
            twain_devices = list_twain_devices(parent_hwnd=parent_hwnd, twain_module=twain_module)
            for device in twain_devices:
                found += 1
                lines.append(f"  - TWAIN: {device.display_name}")
        except Exception as exc:
            lines.append(f"  - TWAIN: غير متاح — {exc}")
    if not found:
        lines.append("  لا توجد أجهزة.")
    last = get_last_error()
    if last is not None:
        lines.extend([
            "",
            "آخر خطأ مسجّل:",
            f"  {last}",
            f"  HRESULT: {('0x%08X' % last.hresult) if last.hresult is not None else 'غير متاح'}",
        ])
    log_dir = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), APP_DIR_NAME, "logs")
    lines.extend(["", f"ملف السجل: {os.path.join(log_dir, 'scanner.log')}"])
    return "\n".join(lines)
