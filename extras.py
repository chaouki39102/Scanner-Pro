# -*- coding: utf-8 -*-
"""ميزات إضافية مستقلة عن الواجهة (قابلة للاختبار منفردة):
- كشف الصفحات الفارغة
- أدوات PDF: دمج، تقسيم، استخراج، حذف، تدوير، حماية بكلمة مرور، ضغط
- التسمية الذكية للملفات
كل شيء محلي بالكامل ولا يحتاج إنترنت ولا مكتبات إضافية (PyMuPDF وOpenCV موجودتان أصلًا).
"""
import os
import re
from datetime import datetime

import cv2
import numpy as np

try:
    import pymupdf as fitz
except ImportError:  # إصدارات قديمة
    import fitz


# ------------------------------------------------------------------ الصفحات الفارغة
def blank_metrics(img):
    """يرجع (نسبة_الحبر, الانحراف_المعياري) لصورة PIL بعد تجاهل الحواف والغبار."""
    g = np.array(img.convert("L"))
    h, w = g.shape
    scale = 800.0 / max(h, w)
    if scale < 1:
        g = cv2.resize(g, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    h, w = g.shape
    my, mx = int(h * 0.06), int(w * 0.06)          # تجاهل حواف الماسح وظلال الأطراف
    if h - 2 * my > 20 and w - 2 * mx > 20:
        g = g[my:h - my, mx:w - mx]
    g = cv2.GaussianBlur(g, (3, 3), 0)
    bg = float(np.median(g))
    dark = (g < bg - 40).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))   # يُسقط الغبار والنقاط المنفردة
    return float(dark.mean()), float(g.std())


def is_blank(img, sensitivity=0.5):
    """هل الصفحة فارغة؟ sensitivity بين 0 و1: الأعلى يعني اعتبار صفحات أكثر «فارغة»."""
    sensitivity = max(0.0, min(1.0, float(sensitivity)))
    ink, std = blank_metrics(img)
    threshold = 0.0004 + sensitivity * 0.006          # من 0.04% إلى 0.64% من مساحة الصفحة
    return ink < threshold and std < 30.0


# ------------------------------------------------------------------ التسمية الذكية
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_name(text, max_len=40):
    text = _BAD.sub(" ", text or "")
    text = re.sub(r"\s+", "_", text.strip())
    text = text.strip("._ ")
    return text[:max_len].rstrip("._ ")


def smart_name(prefix="Scan", first_line=None, add_date=True, now=None, ext=""):
    """مثال: فاتورة_شركة_2026-10-24_1530.pdf"""
    now = now or datetime.now()
    parts = [clean_name(first_line)] if first_line and clean_name(first_line) else [clean_name(prefix) or "Scan"]
    if add_date:
        parts.append(f"{now:%Y-%m-%d_%H%M}")
    return "_".join(p for p in parts if p) + ext


def first_text_line(img, pytesseract_module, lang="ara+fra+eng"):
    """أول سطر نصي ذي معنى من أعلى الصفحة عبر OCR (يعيد None عند الفشل)."""
    try:
        im = img.convert("L")
        w, h = im.size
        im = im.crop((0, 0, w, int(h * 0.35)))              # الترويسة فقط: أسرع وأدق للعنوان
        if im.width > 1600:
            im = im.resize((1600, int(im.height * 1600 / im.width)))
        text = pytesseract_module.image_to_string(im, lang=lang, config="--psm 6")
        for line in text.splitlines():
            line = line.strip()
            if len(re.findall(r"\w", line)) >= 3:
                return line
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ أدوات PDF
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def parse_ranges(spec, total):
    """يحلل نطاقات مثل '1-3,5,8-' (ويقبل الأرقام العربية) إلى قائمة فهارس مبدوءة من 0 بدون تكرار."""
    spec = (spec or "").translate(_AR_DIGITS).replace("،", ",").replace("؛", ",").replace(";", ",").replace(" ", "")
    if not spec:
        raise ValueError("لم تُدخل أرقام صفحات.")
    out = []
    for part in spec.split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else total
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"صيغة غير صحيحة: {part}")
        if a < 1 or b < a or b > total:
            raise ValueError(f"نطاق خارج حدود المستند (1–{total}): {part}")
        out.extend(i - 1 for i in range(a, b + 1))
    seen, uniq = set(), []
    for i in out:
        if i not in seen:
            seen.add(i); uniq.append(i)
    return uniq


def _open(path, password=None):
    doc = fitz.open(path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise ValueError("الملف محميّ بكلمة مرور.")
    return doc


def pdf_page_count(path, password=None):
    with _open(path, password) as d:
        return d.page_count


def merge_pdfs(paths, out_path):
    if len(paths) < 2:
        raise ValueError("اختر ملفين على الأقل.")
    out = fitz.open()
    try:
        for p in paths:
            with _open(p) as d:
                out.insert_pdf(d)
        out.save(out_path, garbage=3, deflate=True)
    finally:
        out.close()
    return out_path


def extract_pages(path, out_path, spec):
    with _open(path) as d:
        idx = parse_ranges(spec, d.page_count)
        out = fitz.open()
        try:
            for i in idx:
                out.insert_pdf(d, from_page=i, to_page=i)
            out.save(out_path, garbage=3, deflate=True)
        finally:
            out.close()
    return out_path


def delete_pages(path, out_path, spec):
    with _open(path) as d:
        idx = parse_ranges(spec, d.page_count)
        if len(idx) >= d.page_count:
            raise ValueError("لا يمكن حذف كل الصفحات.")
        d.delete_pages(sorted(idx))
        d.save(out_path, garbage=3, deflate=True)
    return out_path


def rotate_pages(path, out_path, angle, spec=None):
    if angle % 90:
        raise ValueError("الزاوية يجب أن تكون من مضاعفات 90.")
    with _open(path) as d:
        idx = parse_ranges(spec, d.page_count) if spec else range(d.page_count)
        for i in idx:
            pg = d[i]
            pg.set_rotation((pg.rotation + int(angle)) % 360)
        d.save(out_path, garbage=3, deflate=True)
    return out_path


def split_pdf(path, out_dir, spec=None, every=None):
    """تقسيم: spec مثل '1-3,4-6' (كل جزء ملف) أو every=N لتقسيم كل N صفحات."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    files = []
    with _open(path) as d:
        total = d.page_count
        if every:
            every = int(every)
            if every < 1:
                raise ValueError("عدد الصفحات يجب أن يكون 1 أو أكثر.")
            groups = [list(range(s, min(s + every, total))) for s in range(0, total, every)]
        else:
            norm = (spec or "").translate(_AR_DIGITS).replace("،", ",").replace(";", ",")
            groups = [parse_ranges(part, total) for part in norm.split(",") if part.strip()]
            if not groups:
                raise ValueError("لم تُدخل نطاقات التقسيم.")
        for n, idx in enumerate(groups, 1):
            out = fitz.open()
            try:
                for i in idx:
                    out.insert_pdf(d, from_page=i, to_page=i)
                fp = os.path.join(out_dir, f"{base}_part{n:02d}.pdf")
                out.save(fp, garbage=3, deflate=True)
                files.append(fp)
            finally:
                out.close()
    return files


def protect_pdf(path, out_path, password):
    if not password or len(password) < 4:
        raise ValueError("كلمة المرور قصيرة (4 أحرف على الأقل).")
    with _open(path) as d:
        d.save(out_path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password,
               owner_pw=password + "#owner", permissions=int(fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY),
               garbage=3, deflate=True)
    return out_path


def compress_pdf(path, out_path, image_quality=70):
    """ضغط عبر إعادة ترميز الصور وتنظيف الكائنات. يرجع (الحجم_قبل, الحجم_بعد)."""
    before = os.path.getsize(path)
    with _open(path) as d:
        try:
            d.rewrite_images(dpi_threshold=160, dpi_target=150, quality=int(image_quality))
        except Exception:
            pass                                                  # إصدارات PyMuPDF القديمة بلا rewrite_images
        d.save(out_path, garbage=4, deflate=True, clean=True)
    return before, os.path.getsize(out_path)


def render_pdf_pages(path, dpi=200, password=None):
    """مولّد (رقم_الصفحة, صورة PIL) لاستيراد صفحات PDF كصفحات في الجلسة."""
    from PIL import Image
    with _open(path, password) as d:
        for i, pg in enumerate(d):
            pix = pg.get_pixmap(dpi=dpi, alpha=False)
            yield i + 1, Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
