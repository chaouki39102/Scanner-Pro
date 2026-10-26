#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""قياس دقة استخراج بيانات الفواتير على مجلد فواتير حقيقي.

لا يعرض واجهة رسومية ولا يرفع الصور إلى الإنترنت. يقرأ الصور وPDF، يشغّل
نفس مسار الاستخراج المستخدم في التطبيق، ثم يقارن النتائج بملف truth.csv.

الاستخدام السريع:
    python tools/benchmark_invoices.py --dir invoices --truth truth.csv
    python tools/benchmark_invoices.py --dir invoices --init-truth
"""
import argparse
import csv
import datetime as dt
import difflib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import extras
import invoice_extract as ie
import scan_enhance as se
from PIL import Image, ImageOps

TRUTH_FIELDS = (
    "invoice_number", "date", "supplier", "supplier_nif", "supplier_nis",
    "supplier_rc", "supplier_ai", "client", "ht", "tva", "tva_rate",
    "timbre", "ttc", "net", "items_count",
)
NUMERIC_FIELDS = {"ht", "tva", "tva_rate", "timbre", "ttc", "net", "items_count"}
DIGIT_ID_FIELDS = {"supplier_nif", "supplier_nis", "supplier_ai"}
SUPPORTED_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf"}
AMOUNT_TOLERANCE = 0.01
NAME_THRESHOLD = 0.85

FIELD_LABELS = {
    "invoice_number": "رقم الفاتورة",
    "date": "التاريخ",
    "supplier": "المورّد",
    "supplier_nif": "NIF المورّد",
    "supplier_nis": "NIS المورّد",
    "supplier_rc": "RC المورّد",
    "supplier_ai": "AI المورّد",
    "client": "الزبون",
    "ht": "HT",
    "tva": "TVA",
    "tva_rate": "نسبة TVA %",
    "timbre": "الطابع",
    "ttc": "TTC",
    "net": "الصافي",
    "items_count": "عدد البنود",
}


class BenchmarkError(RuntimeError):
    """خطأ في المدخلات أو إعداد OCR، وليس في استخراج فاتورة واحدة."""


# ---------------------------------------------------------------- أدوات صغيرة
def _norm_text(value):
    return ie.normalize_text("" if value is None else str(value)).strip()


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _norm_text(value)
    if s.endswith("%"):
        s = s[:-1].strip()
    if not s:
        return None
    return ie.parse_amount(s)


def _digits(value):
    return re.sub(r"\D", "", _norm_text(value))


def _rc(value):
    return re.sub(r"\s+", "", _norm_text(value)).casefold()


def _invoice_number(value):
    return re.sub(r"\s+", "", _norm_text(value)).casefold()


_AR_TRANSLATION = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ـ": "",
})


def _name(value):
    s = unicodedata.normalize("NFKD", "" if value is None else str(value))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold().translate(_AR_TRANSLATION)
    return re.sub(r"\s+", " ", s).strip()


def _date(value):
    s = _norm_text(value)
    if not s:
        return ""
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        return s


def compare_field(field, expected, actual):
    """يقارن قيمة متوقعة (نص من truth) بقيمة مستخرجة وفق قواعد المشروع."""
    if field in DIGIT_ID_FIELDS:
        expected_digits = _digits(expected)
        if not expected_digits:
            return not _norm_text(actual)
        return expected_digits == _digits(actual)
    if field == "supplier_rc":
        return _rc(expected) == _rc(actual)
    if field == "invoice_number":
        return _invoice_number(expected) == _invoice_number(actual)
    if field in ("supplier", "client"):
        e, a = _name(expected), _name(actual)
        if not e or not a:
            return e == a
        return difflib.SequenceMatcher(None, e, a, autojunk=False).ratio() >= NAME_THRESHOLD
    if field == "date":
        return _date(expected) == _date(actual)
    if field in NUMERIC_FIELDS:
        e, a = _num(expected), _num(actual)
        if e is None:
            if field == "items_count":
                return a is None or abs(a) < 0.0001
            return a is None
        if a is None:
            return False
        return abs(e - a) <= AMOUNT_TOLERANCE + 1e-9
    e, a = _norm_text(expected), _norm_text(actual)
    return e.casefold() == a.casefold()


def parse_expected(field, raw, line_no):
    raw = (raw or "").strip()
    if raw.casefold() == "skip":
        return {"raw": "SKIP", "value": None, "skip": True}
    if not raw:
        return {"raw": "", "value": None, "skip": False}
    if field in DIGIT_ID_FIELDS and not _digits(raw):
        raise BenchmarkError(
            f"truth.csv سطر {line_no}: {field} يجب أن يحتوي أرقامًا، أو اتركه فارغًا/SKIP."
        )
    if field in NUMERIC_FIELDS:
        value = _num(raw)
        if value is None:
            raise BenchmarkError(f"truth.csv سطر {line_no}: قيمة غير رقمية في {field}: {raw!r}")
        if field == "items_count":
            value = int(round(value))
        return {"raw": raw, "value": value, "skip": False}
    if field == "date":
        try:
            value = dt.date.fromisoformat(raw).isoformat()
        except ValueError:
            raise BenchmarkError(f"truth.csv سطر {line_no}: التاريخ يجب أن يكون ISO مثل 2026-02-03: {raw!r}")
        return {"raw": raw, "value": value, "skip": False}
    return {"raw": raw, "value": raw, "skip": False}


# --------------------------------------------------------------- ملفات الإدخال
def discover_files(directory):
    """يرجع المسارات النسبية للملفات المدعومة، مرتبة."""
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        raise BenchmarkError(f"المجلد غير موجود: {directory}")
    found = []
    for base, dirs, names in os.walk(directory):
        dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=str.casefold)
        for name in sorted(names, key=str.casefold):
            ext = os.path.splitext(name)[1].lower()
            if ext not in SUPPORTED_EXT:
                continue
            rel = os.path.relpath(os.path.join(base, name), directory).replace(os.sep, "/")
            found.append(rel)
    return sorted(found, key=str.casefold)


def _split_page_hint(value):
    """يفصل doc.pdf#p2 أو doc.pdf#page=2 أو doc.pdf#2."""
    value = (value or "").strip()
    m = re.fullmatch(r"(.*?)(?:#(?:p|page=)?(\d+))?", value, flags=re.IGNORECASE)
    if not m:
        return value, None
    base = m.group(1).strip()
    hint = int(m.group(2)) if m.group(2) else None
    return base, hint


def _file_maps(directory):
    files = discover_files(directory)
    abs_map = {rel: os.path.join(directory, rel.replace("/", os.sep)) for rel in files}
    by_norm = {rel.casefold(): rel for rel in files}
    by_base = defaultdict(list)
    for rel in files:
        by_base[os.path.basename(rel).casefold()].append(rel)
    return files, abs_map, by_norm, by_base


def _resolve_file(value, by_norm, by_base):
    value = (value or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = value.lstrip("/")
    if not value:
        return None
    hit = by_norm.get(value.casefold())
    if hit:
        return hit
    base = os.path.basename(value).casefold()
    matches = by_base.get(base) or []
    return matches[0] if len(matches) == 1 else None


def _kind(path):
    return "pdf" if os.path.splitext(path)[1].lower() == ".pdf" else "image"


def _pdf_page_count(rel, path):
    try:
        total = int(extras.pdf_page_count(path))
    except Exception as exc:
        raise BenchmarkError(f"{rel}: تعذر قراءة PDF: {exc}")
    if total < 1:
        raise BenchmarkError(f"{rel}: ملف PDF بلا صفحات.")
    return total


def _parse_pages(rel, spec, total):
    try:
        return extras.parse_ranges(spec, total)
    except Exception as exc:
        raise BenchmarkError(f"{rel}: صفحات غير صالحة ({spec!r}): {exc}")


def load_document_pages(path, cache=None):
    """يرجع [(رقم الصفحة 1-based, صورة RGB)]؛ PDF عبر extras.render_pdf_pages."""
    path = os.path.abspath(path)
    if cache is not None and path in cache:
        return cache[path]
    if _kind(path) == "pdf":
        pages = [(n, im) for n, im in extras.render_pdf_pages(path, 200)]
    else:
        with Image.open(path) as source:
            im = ImageOps.exif_transpose(source).convert("RGB")
        pages = [(1, im)]
    if cache is not None:
        cache[path] = pages
    return pages


# ------------------------------------------------------------------- truth.csv
def read_truth(path):
    if not os.path.isfile(path):
        raise BenchmarkError(f"ملف truth غير موجود: {path}")
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        if "file" not in headers:
            raise BenchmarkError("truth.csv يجب أن يحتوي عمود file.")
        has_pages = "pages" in headers
        field_headers = [f for f in TRUTH_FIELDS if f in headers]
        if not field_headers:
            raise BenchmarkError("truth.csv لا يحتوي أي عمود حقل قابل للقياس.")
        rows = []
        for line_no, raw_row in enumerate(reader, 2):
            row = {(k or "").strip().lower(): v for k, v in raw_row.items() if k is not None}
            file_value = (row.get("file") or "").strip()
            if not file_value:
                continue
            values = {field: parse_expected(field, row.get(field, ""), line_no)
                      for field in field_headers}
            rows.append({
                "line": line_no,
                "file": file_value,
                "pages_spec": (row.get("pages") or "").strip() if has_pages else "",
                "values": values,
            })
    if not rows:
        raise BenchmarkError("truth.csv لا يحتوي صفوفًا صالحة.")
    return rows, has_pages, field_headers


def build_jobs(directory, rows, limit=None):
    """يوسّع صفحات PDF إلى مهام، ويرجع jobs + مشكلات التغطية."""
    directory = os.path.abspath(directory)
    files, abs_map, by_norm, by_base = _file_maps(directory)
    resolved, missing = [], []
    for row in rows:
        base, hint = _split_page_hint(row["file"])
        rel = _resolve_file(base, by_norm, by_base)
        if rel is None:
            missing.append({"file": row["file"], "line": row["line"]})
            continue
        path = abs_map[rel]
        kind = _kind(path)
        total = 1 if kind == "image" else _pdf_page_count(rel, path)
        item = dict(row, rel=rel, abs=path, kind=kind, total_pages=total, page_hint=hint)
        resolved.append(item)

    # صفوف PDF بلا pages/#pN: صف واحد للصفحة الواحدة، أو صف لكل صفحة بالترتيب.
    groups = defaultdict(list)
    for item in resolved:
        if item["kind"] == "pdf" and not item["pages_spec"] and item["page_hint"] is None:
            groups[item["rel"]].append(item)
    for rel, group in groups.items():
        total = group[0]["total_pages"]
        if total == 1:
            for item in group:
                item["auto_pages"] = [0]
        elif len(group) == total:
            for idx, item in enumerate(group):
                item["auto_pages"] = [idx]
        elif len(group) == 1:
            raise BenchmarkError(
                f"{rel}: ملف PDF من {total} صفحات ولا يوجد عمود pages أو #pN. "
                "أضف عمود pages أو صفًا لكل صفحة."
            )
        else:
            raise BenchmarkError(
                f"{rel}: عدد صفوف truth ({len(group)}) لا يساوي عدد صفحات PDF ({total}). "
                "استخدم عمود pages أو #pN."
            )

    jobs = []
    seen = set()
    page_owner = {}
    for item in resolved:
        rel, kind, total = item["rel"], item["kind"], item["total_pages"]
        if kind == "image":
            if item["page_hint"] not in (None, 1):
                raise BenchmarkError(f"{rel}: صورة عادية لا تحتوي صفحة {item['page_hint']}.")
            if item["pages_spec"]:
                pages = _parse_pages(rel, item["pages_spec"], 1)
                if pages != [0]:
                    raise BenchmarkError(f"{rel}: pages غير صالح لصورة عادية.")
            else:
                pages = [0]
        elif item["pages_spec"]:
            pages = _parse_pages(rel, item["pages_spec"], total)
        elif item["page_hint"] is not None:
            if not 1 <= item["page_hint"] <= total:
                raise BenchmarkError(f"{rel}: الصفحة {item['page_hint']} خارج 1–{total}.")
            pages = [item["page_hint"] - 1]
        elif item.get("auto_pages"):
            pages = item["auto_pages"]
        elif total == 1:
            pages = [0]
        else:
            raise BenchmarkError(f"{rel}: صفحات PDF متعددة؛ حدد pages في truth.csv.")
        pages = sorted(set(pages))
        key = (rel, tuple(pages))
        if key in seen:
            raise BenchmarkError(f"{rel}: صف truth مكرر لنفس الصفحات ({pages}).")
        seen.add(key)
        for page in pages:
            owner = page_owner.get((rel, page))
            if owner is not None:
                raise BenchmarkError(
                    f"{rel}: الصفحة {page + 1} مكررة في صفوف truth (السطران {owner} و{item['line']})."
                )
            page_owner[(rel, page)] = item["line"]
        label = ",".join(str(p + 1) for p in pages)
        jobs.append({
            "file": rel,
            "abs": item["abs"],
            "pages": pages,
            "pages_label": label,
            "values": item["values"],
            "truth_line": item["line"],
        })

    jobs.sort(key=lambda j: (j["file"].casefold(), j["pages"]))
    all_jobs = list(jobs)
    if limit:
        jobs = jobs[:limit]

    used_files = {j["file"] for j in all_jobs}
    unlabeled = [rel for rel in files if rel not in used_files]
    covered = defaultdict(set)
    for job in all_jobs:
        covered[job["file"]].update(job["pages"])
    uncovered = []
    for rel in sorted(used_files, key=str.casefold):
        if _kind(rel) != "pdf":
            continue
        total = _pdf_page_count(rel, abs_map[rel])
        missing_pages = [p + 1 for p in range(total) if p not in covered[rel]]
        if missing_pages:
            uncovered.append({"file": rel, "pages": missing_pages})
    return jobs, missing, unlabeled, uncovered, len(all_jobs)


# --------------------------------------------------------------------- OCR
def _find_tesseract():
    try:
        import pytesseract
    except ImportError:
        raise BenchmarkError("pytesseract غير مثبت. ثبّت متطلبات المشروع أولًا.")

    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(ROOT, "tesseract", "tesseract.exe"),
    ]
    for env_name in ("LOCALAPPDATA", "ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name, "").strip()
        if base:
            candidates.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Tesseract-OCR", 0, winreg.KEY_READ | view) as k:
                        candidates.append(os.path.join(winreg.QueryValueEx(k, "InstallDir")[0], "tesseract.exe"))
                except OSError:
                    pass
    except ImportError:
        pass
    current = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
    if current and os.path.isfile(current):
        candidates.insert(0, current)
    found = shutil.which("tesseract.exe") or shutil.which("tesseract")
    if found:
        candidates.insert(0, found)
    exe = next((os.path.normpath(x) for x in candidates if x and os.path.isfile(x)), None)
    if not exe:
        raise BenchmarkError("لم أجد tesseract.exe. استخدم --tessdata-dir أو ثبّت Tesseract.")
    pytesseract.pytesseract.tesseract_cmd = exe
    return pytesseract, exe


def _valid_tessdata(path):
    try:
        return os.path.isdir(path) and any(
            name.lower().endswith(".traineddata") for name in os.listdir(path)
        )
    except OSError:
        return False


def _find_tessdata(exe, explicit):
    if explicit:
        path = os.path.abspath(explicit)
        if _valid_tessdata(path):
            return path
        nested = os.path.join(path, "tessdata")
        if _valid_tessdata(nested):
            return nested
        raise BenchmarkError(f"مجلد tessdata غير صالح: {path}")
    exe_dir = os.path.dirname(exe)
    candidates = [
        os.path.join(exe_dir, "tessdata"),
        os.path.join(os.path.dirname(exe_dir), "share", "tessdata"),
    ]
    env_prefix = os.environ.get("TESSDATA_PREFIX", "").strip()
    if env_prefix:
        candidates += [os.path.join(env_prefix, "tessdata"), env_prefix]
    tessdata = next((os.path.normpath(p) for p in candidates if p and _valid_tessdata(p)), None)
    if not tessdata:
        raise BenchmarkError("لم أجد مجلد tessdata يحتوي ملفات .traineddata.")
    return tessdata


def _tesseract_setup(lang, tessdata_dir=None):
    pytesseract, exe = _find_tesseract()
    tessdata = _find_tessdata(exe, tessdata_dir)
    exe_tessdata = os.path.join(os.path.dirname(exe), "tessdata")
    td_config = ""
    if os.path.normcase(os.path.abspath(tessdata)) != os.path.normcase(os.path.abspath(exe_tessdata)):
        td_config = f'--tessdata-dir "{tessdata}"'
    os.environ.pop("TESSDATA_PREFIX", None)
    try:
        available = set(pytesseract.get_languages(config=td_config))
    except Exception as exc:
        raise BenchmarkError(f"تعذر قراءة لغات Tesseract: {exc}")
    requested = [x for x in str(lang or "ara+fra+eng").split("+") if x]
    chosen = [x for x in requested if x in available]
    if not chosen:
        raise BenchmarkError(
            f"لا توجد أي لغة مطلوبة في tessdata. المطلوب: {requested}، المتاح: {sorted(available)}"
        )
    missing = [x for x in requested if x not in available]
    if missing:
        print(f"تحذير: لغات غير متوفرة وستُتجاهل: {', '.join(missing)}", file=sys.stderr)
    return pytesseract, "+".join(chosen), td_config, exe, tessdata


def _extract_group(images, one_image):
    texts, res = [], None
    for im in images:
        res = one_image(im)
        texts.append(res.get("text") or "")
    if len(images) > 1:
        res = ie.extract_invoice("\n".join(texts))
        res["text"] = "\n".join(texts)
    return res


def build_extractor(engine="tesseract", lang="ara+fra+eng", tessdata_dir=None):
    """يرجع (دالة تستقبل صور الفاتورة, metadata)."""
    engine = (engine or "tesseract").lower()
    if engine == "rapidocr":
        if not se.rapidocr_available():
            raise BenchmarkError("RapidOCR غير مثبت. ثبّت rapidocr-onnxruntime وonnxruntime.")
        if tessdata_dir:
            print("ملاحظة: --tessdata-dir لا يُستخدم مع RapidOCR.", file=sys.stderr)
        def one(im):
            text = se.rapidocr_text(im)
            res = ie.extract_invoice(text)
            res["text"] = ie.best_text(text)
            return res
        return (lambda images: _extract_group(images, one)), {
            "engine": "rapidocr", "lang": None, "tesseract": None, "tessdata": None,
        }
    if engine != "tesseract":
        raise BenchmarkError(f"محرك غير معروف: {engine}")
    pytesseract, chosen, td_config, exe, tessdata = _tesseract_setup(lang, tessdata_dir)
    def one(im):
        return ie.extract_from_image(im, pytesseract, chosen, td_config)
    return (lambda images: _extract_group(images, one)), {
        "engine": "tesseract", "lang": chosen, "tesseract": exe, "tessdata": tessdata,
    }


# ------------------------------------------------------------------ المقارنة
def _actual_value(field, res):
    fields = res.get("fields") or {}
    if field == "items_count":
        value = fields.get("items_count")
        return len(res.get("items") or []) if value is None else value
    if field == "tva_rate" and fields.get("tva_rate") is None:
        rates = fields.get("tva_rates") or []
        return rates[0] if len(rates) == 1 else None
    return fields.get(field)


def compare_invoice(values, res):
    comparisons = []
    for field, expected in values.items():
        if expected["skip"]:
            comparisons.append({
                "field": field, "expected": "SKIP", "actual": _actual_value(field, res),
                "status": "skipped", "correct": None,
            })
            continue
        actual = _actual_value(field, res)
        correct = compare_field(field, expected["raw"], actual)
        comparisons.append({
            "field": field, "expected": expected["raw"], "actual": actual,
            "status": "correct" if correct else "wrong", "correct": bool(correct),
        })
    return comparisons


def _empty_result(error=None):
    res = {"fields": {}, "items": [], "warnings": [], "score": 0, "text": ""}
    if error:
        res["ocr_error"] = error
        res["warnings"] = [("error", "ocr", error)]
    return res


def _warning_items(res):
    out = []
    for item in res.get("warnings") or []:
        try:
            level, field, message = item
        except Exception:
            continue
        if level:
            out.append({"level": level, "field": field, "message": message})
    return out


# ------------------------------------------------------------------ التقارير
def _pct(correct, total):
    return round(100.0 * correct / total, 2) if total else None


def _fmt_pct(value):
    return "—" if value is None else f"{value:.2f}%"


def _fmt_value(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _md(value):
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _summarize(invoices, meta, missing, unlabeled, uncovered, total_jobs):
    stats = {f: {"correct": 0, "total": 0} for f in TRUTH_FIELDS}
    for inv in invoices:
        for comp in inv["fields"]:
            if comp["status"] == "skipped":
                continue
            stats[comp["field"]]["total"] += 1
            if comp["correct"]:
                stats[comp["field"]]["correct"] += 1
    for field in stats:
        stats[field]["accuracy"] = _pct(stats[field]["correct"], stats[field]["total"])

    overall_correct = sum(v["correct"] for v in stats.values())
    overall_total = sum(v["total"] for v in stats.values())
    scored = [inv for inv in invoices if inv["scored"]]
    full_correct = [inv for inv in scored if inv["full_correct"]]
    incorrect = [inv for inv in scored if not inv["full_correct"]]
    warned = [inv for inv in incorrect if inv["warned"]]
    false_alarms = [inv for inv in full_correct if inv["warned"]]
    elapsed = [inv["elapsed_sec"] for inv in invoices]
    worst = sorted(
        (inv for inv in scored if not inv["full_correct"]),
        key=lambda inv: (-inv["wrong_count"], -inv["compared_count"], inv["file"].casefold(), inv["pages"]),
    )[:10]
    errors = [{"file": inv["file"], "pages": inv["pages"], "error": inv["error"]}
              for inv in invoices if inv["error"]]
    return {
        "meta": meta,
        "summary": {
            "invoices_total": len(invoices),
            "invoices_scored": len(scored),
            "invoices_error": len(errors),
            "jobs_total": total_jobs,
            "jobs_run": len(invoices),
            "fields": stats,
            "overall": {
                "correct": overall_correct, "total": overall_total,
                "accuracy": _pct(overall_correct, overall_total),
            },
            "full_invoice": {
                "correct": len(full_correct), "total": len(scored),
                "accuracy": _pct(len(full_correct), len(scored)),
            },
            "warnings": {
                "incorrect_total": len(incorrect),
                "incorrect_flagged": len(warned),
                "incorrect_rate": _pct(len(warned), len(incorrect)),
                "correct_total": len(full_correct),
                "correct_flagged": len(false_alarms),
                "false_alarm_rate": _pct(len(false_alarms), len(full_correct)),
            },
            "timing": {
                "average_sec": round(sum(elapsed) / len(elapsed), 3) if elapsed else 0.0,
                "max_sec": round(max(elapsed), 3) if elapsed else 0.0,
                "total_sec": round(sum(elapsed), 3),
            },
            "worst": worst,
            "errors": errors,
        },
        "invoices": invoices,
        "missing_files": missing,
        "unlabeled_files": unlabeled,
        "uncovered_pages": uncovered,
    }


def _write_reports(out_prefix, report):
    base = os.path.abspath(out_prefix)
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    csv_path, json_path, md_path = base + ".csv", base + ".json", base + ".md"
    s = report["summary"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["invoice", "file", "pages", "field", "expected", "actual",
                         "status", "invoice_ok", "warnings", "elapsed_sec"])
        for idx, inv in enumerate(report["invoices"], 1):
            for comp in inv["fields"]:
                writer.writerow([
                    idx, inv["file"], inv["pages"], comp["field"], comp["expected"],
                    "" if comp["actual"] is None else comp["actual"], comp["status"],
                    "yes" if inv["full_correct"] else "no",
                    "|".join(inv["warning_levels"]), f"{inv['elapsed_sec']:.3f}",
                ])
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    lines = [
        "# تقرير قياس دقة استخراج الفواتير",
        "",
        f"- المجلد: `{_md(report['meta']['directory'])}`",
        f"- truth: `{_md(report['meta']['truth'])}`",
        f"- المحرك: `{_md(report['meta']['engine'])}`",
        f"- اللغة: `{_md(report['meta'].get('lang') or '—')}`",
        f"- التاريخ: {_md(report['meta']['generated_at'])}",
        "",
        "## الملخص",
        "",
        "| المقياس | القيمة |",
        "|---|---:|",
        f"| الفواتير المُقيّمة | {s['invoices_scored']} |",
        f"| دقة الحقول الإجمالية | {_fmt_pct(s['overall']['accuracy'])} ({s['overall']['correct']}/{s['overall']['total']}) |",
        f"| دقة الفاتورة الكاملة | {_fmt_pct(s['full_invoice']['accuracy'])} ({s['full_invoice']['correct']}/{s['full_invoice']['total']}) |",
        f"| متوسط الزمن | {s['timing']['average_sec']:.3f} ثانية |",
        f"| أقصى زمن | {s['timing']['max_sec']:.3f} ثانية |",
        "",
        "## دقة كل حقل",
        "",
        "| الحقل | الصحيح | المجموع | الدقة |",
        "|---|---:|---:|---:|",
    ]
    for field in TRUTH_FIELDS:
        st = s["fields"][field]
        lines.append(f"| {FIELD_LABELS[field]} | {st['correct']} | {st['total']} | {_fmt_pct(st['accuracy'])} |")
    lines += [
        "",
        "## التنبيهات",
        "",
        f"- من الفواتير الخاطئة ({s['warnings']['incorrect_total']}): ظهر تنبيه error/warn في "
        f"{s['warnings']['incorrect_flagged']} ({_fmt_pct(s['warnings']['incorrect_rate'])}).",
        f"- من الفواتير الصحيحة ({s['warnings']['correct_total']}): ظهر إنذار كاذب في "
        f"{s['warnings']['correct_flagged']} ({_fmt_pct(s['warnings']['false_alarm_rate'])}).",
        "",
        "## أسوأ 10 فواتير",
        "",
    ]
    if s["worst"]:
        lines += ["| # | الملف | الصفحات | حقول خاطئة | التفاصيل |", "|---:|---|---:|---:|---|"]
        for i, inv in enumerate(s["worst"], 1):
            details = "<br>".join(
                f"{FIELD_LABELS.get(c['field'], c['field'])}: متوقع `{_md(_fmt_value(c['expected']))}` / "
                f"مستخرج `{_md(_fmt_value(c['actual']))}`"
                for c in inv["fields"] if c["status"] == "wrong"
            )
            if inv["error"]:
                details = (details + "<br>" if details else "") + f"خطأ: `{_md(inv['error'])}`"
            lines.append(f"| {i} | `{_md(inv['file'])}` | {_md(inv['pages'])} | {inv['wrong_count']} | {details} |")
    else:
        lines.append("لا توجد فواتير خاطئة في العينة.")
    lines += ["", "## ملاحظات", ""]
    lines.append("- الحقل الفارغ في truth يعني «غير موجود في الفاتورة»؛ واستخراج قيمة له يُعدّ خطأ.")
    lines.append("- قيمة `SKIP` تُستبعد من حساب الدقة.")
    lines.append("- `items_count = 0` يُعتبر غيابًا عندما يكون الحقل فارغًا في truth.")
    if report["meta"].get("limited"):
        lines.append(f"- تم تطبيق `--limit`: شُغّلت {s['jobs_run']} من {s['jobs_total']} مهمة.")
    if report["missing_files"]:
        lines += ["", "## ملفات truth غير موجودة", ""]
        for item in report["missing_files"]:
            lines.append(f"- سطر {item['line']}: `{_md(item['file'])}`")
    if report["unlabeled_files"]:
        lines += ["", "## ملفات بلا صف truth", ""]
        for rel in report["unlabeled_files"]:
            lines.append(f"- `{_md(rel)}`")
    if report["uncovered_pages"]:
        lines += ["", "## صفحات PDF بلا تغطية", ""]
        for item in report["uncovered_pages"]:
            lines.append(f"- `{_md(item['file'])}`: {', '.join(map(str, item['pages']))}")
    if report["summary"]["errors"]:
        lines += ["", "## أخطاء أثناء التشغيل", ""]
        for item in report["summary"]["errors"]:
            lines.append(f"- `{_md(item['file'])}` [{_md(item['pages'])}]: {_md(item['error'])}")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return csv_path, json_path, md_path


def print_summary(report):
    s = report["summary"]
    print("=== قياس دقة استخراج الفواتير ===")
    print(f"الفواتير: {s['invoices_scored']} مُقيّمة / {s['invoices_total']} مهمة"
          f" | أخطاء OCR: {s['invoices_error']}")
    print(f"الحقول: {_fmt_pct(s['overall']['accuracy'])} "
          f"({s['overall']['correct']}/{s['overall']['total']})")
    print(f"الفاتورة الكاملة: {_fmt_pct(s['full_invoice']['accuracy'])} "
          f"({s['full_invoice']['correct']}/{s['full_invoice']['total']})")
    print("أضعف الحقول:")
    weak = sorted(
        (f for f in TRUTH_FIELDS if s["fields"][f]["total"]),
        key=lambda f: (s["fields"][f]["accuracy"], -s["fields"][f]["total"]),
    )[:5]
    if not weak:
        print("  — لا توجد حقول قابلة للقياس.")
    for field in weak:
        st = s["fields"][field]
        print(f"  - {FIELD_LABELS[field]}: {_fmt_pct(st['accuracy'])} ({st['correct']}/{st['total']})")
    w = s["warnings"]
    print(f"تنبيهات الفواتير الخاطئة: {_fmt_pct(w['incorrect_rate'])} "
          f"({w['incorrect_flagged']}/{w['incorrect_total']})")
    print(f"الإنذارات الكاذبة: {_fmt_pct(w['false_alarm_rate'])} "
          f"({w['correct_flagged']}/{w['correct_total']})")
    print(f"الزمن: متوسط {s['timing']['average_sec']:.3f} ثانية، أقصى {s['timing']['max_sec']:.3f} ثانية")
    if s["worst"]:
        print("أسوأ الفواتير:")
        for inv in s["worst"]:
            wrong = ", ".join(FIELD_LABELS.get(c["field"], c["field"])
                              for c in inv["fields"] if c["status"] == "wrong")
            print(f"  - {inv['file']} [{inv['pages']}]: {inv['wrong_count']} خطأ ({wrong or inv['error'] or '—'})")
    if report["missing_files"]:
        print(f"ملفات truth مفقودة: {len(report['missing_files'])}")
    if report["unlabeled_files"]:
        print(f"ملفات بلا truth: {len(report['unlabeled_files'])}")
    if report["uncovered_pages"]:
        print(f"ملفات PDF بصفحات غير مغطاة: {len(report['uncovered_pages'])}")


# ------------------------------------------------------------------- التشغيل
def run_benchmark(directory, truth_path, lang="ara+fra+eng", engine="tesseract",
                  tessdata_dir=None, out="report", limit=None, extractor=None,
                  extractor_meta=None):
    """يشغّل القياس ويعيد بنية التقرير. extractor اختياري للاختبارات."""
    rows, has_pages, field_headers = read_truth(truth_path)
    jobs, missing, unlabeled, uncovered, total_jobs = build_jobs(
        directory, rows, limit=limit
    )
    if not jobs:
        raise BenchmarkError("لا توجد مهام قابلة للتقييم بعد مطابقة truth مع الملفات.")
    if extractor is None:
        extractor, engine_meta = build_extractor(engine, lang, tessdata_dir)
    else:
        engine_meta = extractor_meta or {"engine": "custom", "lang": None,
                                         "tesseract": None, "tessdata": None}
    cache = {}
    previous_abs = None
    invoices = []
    for idx, job in enumerate(jobs, 1):
        started = time.perf_counter()
        error = None
        try:
            if previous_abs != job["abs"]:
                cache.clear()
                previous_abs = job["abs"]
            document_pages = load_document_pages(job["abs"], cache)
            selected = [im for page_no, im in document_pages if page_no - 1 in job["pages"]]
            if len(selected) != len(job["pages"]):
                raise RuntimeError(f"تعذر تحميل كل الصفحات المطلوبة ({len(selected)}/{len(job['pages'])}).")
            result = extractor(selected)
            if not isinstance(result, dict):
                raise RuntimeError("المستخرج أعاد نتيجة غير صالحة.")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            result = _empty_result(error)
        elapsed = time.perf_counter() - started
        fields = result.setdefault("fields", {})
        result.setdefault("items", [])
        result.setdefault("warnings", [])
        comparisons = compare_invoice(job["values"], result)
        compared = [c for c in comparisons if c["status"] != "skipped"]
        wrong = [c for c in comparisons if c["status"] == "wrong"]
        warning_items = _warning_items(result)
        warning_levels = sorted({w["level"] for w in warning_items if w["level"] in ("error", "warn")})
        scored = bool(compared)
        invoices.append({
            "index": idx,
            "file": job["file"],
            "pages": job["pages_label"],
            "truth_line": job["truth_line"],
            "elapsed_sec": round(elapsed, 3),
            "error": error,
            "ocr_error": result.get("ocr_error"),
            "warnings": warning_items,
            "warning_levels": warning_levels,
            "warned": bool(warning_levels),
            "fields": comparisons,
            "compared_count": len(compared),
            "wrong_count": len(wrong),
            "correct_count": len(compared) - len(wrong),
            "scored": scored,
            "full_correct": bool(scored and not wrong and not error),
            "extracted": fields,
            "items": result.get("items") or [],
            "score": result.get("score"),
        })
    meta = {
        "directory": os.path.abspath(directory),
        "truth": os.path.abspath(truth_path),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "engine": engine_meta.get("engine"),
        "lang": engine_meta.get("lang"),
        "tesseract": engine_meta.get("tesseract"),
        "tessdata": engine_meta.get("tessdata"),
        "has_pages_column": has_pages,
        "field_headers": field_headers,
        "limited": bool(limit),
        "limit": limit,
    }
    report = _summarize(invoices, meta, missing, unlabeled, uncovered, total_jobs)
    csv_path, json_path, md_path = _write_reports(out, report)
    report["meta"]["reports"] = {"csv": csv_path, "json": json_path, "md": md_path}
    # أعِد كتابة JSON بعد إضافة مسارات التقارير.
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def init_truth(directory, truth_path, force=False):
    """ينشئ truth.csv فارغًا من ملفات المجلد؛ PDF صف لكل صفحة."""
    files, abs_map, _by_norm, _by_base = _file_maps(directory)
    if not files:
        raise BenchmarkError("لا توجد ملفات PNG/JPG/TIFF/PDF في المجلد.")
    if os.path.exists(truth_path) and not force:
        raise BenchmarkError(f"الملف موجود مسبقًا: {truth_path} (استخدم --force للكتابة فوقه)")
    rows = []
    has_pdf = False
    for rel in files:
        path = abs_map[rel]
        if _kind(path) == "pdf":
            has_pdf = True
            for page in range(1, _pdf_page_count(rel, path) + 1):
                rows.append([rel, str(page)])
        else:
            rows.append([rel, ""])
    headers = ["file"] + (["pages"] if has_pdf else []) + list(TRUTH_FIELDS)
    os.makedirs(os.path.dirname(os.path.abspath(truth_path)) or ".", exist_ok=True)
    with open(truth_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for rel, page in rows:
            if has_pdf:
                writer.writerow([rel, page] + [""] * len(TRUTH_FIELDS))
            else:
                writer.writerow([rel] + [""] * len(TRUTH_FIELDS))
    print(f"أُنشئ {truth_path} — {len(rows)} صفًا.")
    return truth_path, len(rows)


# ------------------------------------------------------------------------ CLI
def _positive_int(value):
    try:
        value = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("يجب أن يكون عددًا صحيحًا موجبًا")
    if value < 1:
        raise argparse.ArgumentTypeError("يجب أن يكون أكبر من صفر")
    return value


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    parser = argparse.ArgumentParser(
        description="قياس دقة استخراج الفواتير محليًا (لا يرفع الصور إلى الإنترنت)."
    )
    parser.add_argument("--dir", required=True, help="مجلد الصور/PDF")
    parser.add_argument("--truth", help="ملف truth.csv (افتراضي: <dir>/truth.csv)")
    parser.add_argument("--lang", default="ara+fra+eng", help="لغات Tesseract، افتراضي ara+fra+eng")
    parser.add_argument("--engine", choices=("tesseract", "rapidocr"), default="tesseract")
    parser.add_argument("--tessdata-dir", help="مجلد tessdata صريح")
    parser.add_argument("--out", default="report", help="بادئة ملفات التقرير، افتراضي report")
    parser.add_argument("--limit", type=_positive_int, help="أقصى عدد فواتير للتقييم")
    parser.add_argument("--init-truth", action="store_true", help="إنشاء truth.csv فارغ من المجلد")
    parser.add_argument("--force", action="store_true", help="السماح بكتابة truth.csv فوق ملف موجود")
    args = parser.parse_args(argv)
    truth_path = args.truth or os.path.join(args.dir, "truth.csv")
    try:
        if args.init_truth:
            init_truth(args.dir, truth_path, force=args.force)
            return 0
        report = run_benchmark(
            args.dir, truth_path, lang=args.lang, engine=args.engine,
            tessdata_dir=args.tessdata_dir, out=args.out, limit=args.limit,
        )
        print_summary(report)
        paths = report["meta"]["reports"]
        print(f"التقارير: {paths['csv']} | {paths['json']} | {paths['md']}")
        return 0
    except BenchmarkError as exc:
        print(f"خطأ: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("أُوقف بواسطة المستخدم.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
