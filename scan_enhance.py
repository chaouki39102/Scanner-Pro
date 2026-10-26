# -*- coding: utf-8 -*-
"""
scan_enhance.py - محرك معالجة المستندات الممسوحة (بدون واجهة).
خطوات المعالجة:
  1) قص حدود الورقة (تصحيح المنظور) إن وُجدت خلفية واضحة
  2) إزالة الحواف السوداء
  3) إزالة الظلال وتوحيد الإضاءة (Background division)
  4) تصحيح الميلان بدقة (Projection profile)
  5) تقليل التشويش (اختياري)
  6) فلتر نهائي: Magic Color / أبيض وأسود نظيف / رمادي / بدون فلتر
"""
import os
import sys
import urllib.request
import cv2
import numpy as np
from PIL import Image

cv2.setUseOptimized(True)

MODES = ("magic", "bw", "gray", "original")


# ----------------------------------------------------------------- أدوات
def load_bgr(path):
    # """قراءة آمنة تدعم المسارات العربية على ويندوز."""
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("تعذر قراءة الصورة الممسوحة.")
    return img


def rotate_quarters(img, k):
    """تدوير بزوايا قائمة: k ربع دورة مع عقارب الساعة."""
    k %= 4
    if k == 1:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if k == 2:
        return cv2.rotate(img, cv2.ROTATE_180)
    if k == 3:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def _shrink(img, max_side):
    h, w = img.shape[:2]
    s = min(1.0, max_side / float(max(h, w)))
    if s < 1.0:
        img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                         interpolation=cv2.INTER_AREA)
    return img, s


def original_preview(path, rot=0, max_side=2000):
    """معاينة الصورة الأصلية (قبل أي معالجة) مقروءة من الملف الأصلي.

    تُستخدم في مسار التراجع/الإعادة حتى لا نحتاج لتخزين نسخة رابعة من الصورة
    في سجل التاريخ (وهو ما كان يضاعف استهلاك الذاكرة).
    """
    base = rotate_quarters(load_bgr(path), rot)
    return bgr_to_pil(_shrink(base, max_side)[0])


def _to_gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _order_pts(pts):
    pts = np.array(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def bgr_to_pil(img):
    if img.ndim == 2:
        return Image.fromarray(img)
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))


# ------------------------------------------------------------ محرك OCR
_RAPID = None
_RAPID_TRIED = False


def rapidocr_available():
    """هل RapidOCR (وonnxruntime) مثبتان؟ نجرب الصيغتين المعروفتين."""
    try:
        import importlib.util
        return (importlib.util.find_spec("rapidocr") is not None or
                importlib.util.find_spec("rapidocr_onnxruntime") is not None)
    except Exception:
        return False


def get_rapidocr():
    """يحضّر RapidOCR مرة واحدة فقط (تحميل النماذج بطيء)."""
    global _RAPID, _RAPID_TRIED
    if _RAPID is not None:
        return _RAPID
    if _RAPID_TRIED:
        raise RuntimeError("تعذر تحميل RapidOCR سابقًا. ثبّت rapidocr-onnxruntime وأعد المحاولة.")
    _RAPID_TRIED = True
    try:
        from rapidocr import RapidOCR  # الإصدار الجديد الموحّد
    except Exception:
        try:
            from rapidocr_onnxruntime import RapidOCR  # الإصدار الكلاسيكي
        except Exception as exc:
            raise RuntimeError("RapidOCR غير مثبت. شغّل: pip install rapidocr-onnxruntime") from exc
    try:
        _RAPID = RapidOCR()
    except Exception as exc:
        raise RuntimeError("فشل تهيئة RapidOCR: %s" % exc) from exc
    return _RAPID


def _rapid_run(pil_img):
    """يشغّل RapidOCR على صورة PIL أو مصفوفة ويعيد [(box, text, score)]."""
    engine = get_rapidocr()
    if isinstance(pil_img, np.ndarray):
        arr = pil_img if pil_img.ndim == 3 else cv2.cvtColor(pil_img, cv2.COLOR_GRAY2RGB)
        if arr.shape[2] == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB) if arr.dtype == np.uint8 else arr[:, :, :3]
    else:
        arr = np.array(pil_img.convert("RGB"))
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    try:
        out = engine(bgr)
    except Exception:
        out = engine(arr)
    # صيغة الاستجابة تختلف بين الإصدارات: (result, elapse) أو كائن له .txts/.boxes
    lines = []
    if isinstance(out, tuple):
        result = out[0]
    elif hasattr(out, "boxes"):
        result = [list(zip(out.boxes, out.txts, out.scores))] if out.boxes is not None else []
        result = result[0] if result else []
    else:
        result = out
    if not result:
        return lines
    for item in result:
        try:
            box, text, score = item[0], item[1], item[2]
            lines.append((np.array(box, dtype=np.float32).reshape(-1, 2), str(text), float(score)))
        except Exception:
            continue
    return lines


def _lines_to_text(lines):
    return "\n".join(text for _box, text, _score in lines)


def rapidocr_text(pil_img):
    """يعيد نص الصورة الكامل باستخدام RapidOCR."""
    return _lines_to_text(_rapid_run(pil_img))


def rapidocr_lines(pil_img):
    """يعيد [(box, text, score)] لاستخدامها في طبقة نص قابلة للبحث."""
    return _rapid_run(pil_img)


# ------------------------------------------------------- قص الورقة / الحواف
def crop_page(img):
    """يقص الورقة ويصحح المنظور فقط إذا كانت هناك خلفية داكنة واضحة حولها."""
    small, s = _shrink(img, 800)
    g = cv2.GaussianBlur(_to_gray(small), (5, 5), 0)
    h, w = g.shape
    b = max(3, int(min(h, w) * 0.02))
    border = np.concatenate([g[:b].ravel(), g[-b:].ravel(),
                             g[:, :b].ravel(), g[:, -b:].ravel()])
    center = g[h // 4:3 * h // 4, w // 4:3 * w // 4]
    if float(np.median(border)) > 0.7 * float(np.median(center)):
        return img  # لا توجد خلفية مميزة: لا نقص شيئًا (حماية من القص الخاطئ)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return img
    c = max(cnts, key=cv2.contourArea)
    ratio = cv2.contourArea(c) / float(h * w)
    if ratio < 0.35 or ratio > 0.985:
        return img
    hull = cv2.convexHull(c)
    approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
    quad = approx.reshape(4, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
    quad = _order_pts(quad) / s
    tl, tr, br, bl = quad
    W = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    H = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if W < 200 or H < 200:
        return img
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, m, (W, H), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def trim_dark_edges(img, max_frac=0.08):
    """يزيل الأشرطة الداكنة الرفيعة عند أطراف الصورة (حواف الماسح)."""
    gray = _to_gray(img)
    h, w = gray.shape
    ref = float(np.percentile(gray[::4, ::4], 90))
    thr = 0.4 * ref
    rows = gray.mean(axis=1)
    cols = gray.mean(axis=0)

    def first_ok(arr, limit):
        for i in range(min(limit, len(arr))):
            if arr[i] >= thr:
                return i
        return limit

    top = first_ok(rows, int(h * max_frac))
    bottom = first_ok(rows[::-1], int(h * max_frac))
    left = first_ok(cols, int(w * max_frac))
    right = first_ok(cols[::-1], int(w * max_frac))
    pad = lambda v: v + 2 if v > 0 else 0
    top, bottom, left, right = pad(top), pad(bottom), pad(left), pad(right)
    if top + bottom + left + right == 0:
        return img
    out = img[top:h - bottom, left:w - right]
    if out.shape[0] < h * 0.5 or out.shape[1] < w * 0.5:
        return img
    return out


# ------------------------------------------------------------ إزالة الظلال
def flatten_illumination(img, passes=2):
    """تقدير خلفية الورقة (الإضاءة/الظلال) ثم القسمة عليها => خلفية بيضاء متجانسة.
    التمريرة الثانية تزيل بقايا الظلال التي لم تُزل في الأولى."""
    for _ in range(max(1, passes)):
        img = _flatten_once(img)
    return img


def _flatten_once(img):
    h, w = img.shape[:2]
    small, _ = _shrink(img, 640)
    k = max(9, (max(small.shape[:2]) // 30) | 1)
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg = cv2.dilate(small, se)            # يمحو الحبر ويُبقي لون الورقة
    bg = cv2.medianBlur(bg, 21)
    bg = cv2.GaussianBlur(bg, (0, 0), max(2.0, k / 3.0))
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    bg = np.maximum(bg, 50)               # تجنّب تضخيم المناطق المظلمة جدًا
    return cv2.divide(img, bg, scale=255.0)


# ----------------------------------------------------------- تصحيح الميلان
def estimate_skew(img, max_angle=8.0):
    """يعيد الزاوية (بدرجات، موجبة = عكس عقارب الساعة) اللازمة لتعديل الصفحة."""
    small, _ = _shrink(img, 700)
    g = cv2.GaussianBlur(_to_gray(small), (3, 3), 0)
    bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 25, 12)
    h, w = bw.shape
    m = max(2, int(min(h, w) * 0.03))
    bw = bw[m:h - m, m:w - m]
    if cv2.countNonZero(bw) < 80:
        return 0.0
    hh, ww = bw.shape
    center = (ww / 2.0, hh / 2.0)

    def score(a):
        mat = cv2.getRotationMatrix2D(center, a, 1.0)
        r = cv2.warpAffine(bw, mat, (ww, hh), flags=cv2.INTER_NEAREST)
        rows = r.sum(axis=1, dtype=np.float64)
        cols = r.sum(axis=0, dtype=np.float64)
        # يعمل للنص الأفقي ولخطوط الجداول الأفقية والرأسية معًا
        return float(np.var(np.diff(rows)) + np.var(np.diff(cols)))

    best_a, best_s = 0.0, -1.0
    for a in np.arange(-max_angle, max_angle + 0.01, 0.5):
        sc = score(a)
        if sc > best_s:
            best_a, best_s = float(a), sc
    for step, span in ((0.1, 0.5), (0.02, 0.1)):
        base = best_a
        for a in np.arange(base - span, base + span + 1e-9, step):
            sc = score(a)
            if sc > best_s:
                best_a, best_s = float(a), sc
    if best_s < score(0.0) * 1.03 or abs(best_a) < 0.12:
        return 0.0
    return best_a


def rotate_keep(img, angle, fill=255):
    h, w = img.shape[:2]
    mat = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    bv = (fill, fill, fill) if img.ndim == 3 else fill
    return cv2.warpAffine(img, mat, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=bv)


# ------------------------------------------------ الذكاء الاصطناعي (اختياري)
MODEL_URL = "https://github.com/fannymonori/TF-ESPCN/raw/master/export/ESPCN_x2.pb"
_SR = None


def models_dir():
    d = os.path.join(os.path.expanduser("~"), ".canon_scan_pro")
    os.makedirs(d, exist_ok=True)
    return d


def ai_available():
    return hasattr(cv2, "dnn_superres")


def get_sr():
    """يحمّل نموذج ESPCN (شبكة عصبية خفيفة لرفع الدقة 2×) ويحفظه محليًا مرة واحدة."""
    global _SR
    if _SR is not None:
        return _SR
    if not ai_available():
        raise RuntimeError("مكتبة opencv-contrib-python غير مثبتة (شغّل install.bat).")
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(base, "assets", "models", "ESPCN_x2.pb")
    path = bundled if os.path.exists(bundled) and os.path.getsize(bundled) >= 50000 \
        else os.path.join(models_dir(), "ESPCN_x2.pb")
    if not os.path.exists(path) or os.path.getsize(path) < 50000:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())
        if os.path.getsize(path) < 50000:
            os.remove(path)
            raise RuntimeError("فشل تنزيل نموذج الذكاء الاصطناعي.")
    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(path)
    sr.setModel("espcn", 2)
    _SR = sr
    return sr


def ai_upscale_gray(gray, sr, tile=384, pad=8):
    """رفع الدقة 2× على مربعات صغيرة لتوفير الذاكرة."""
    h, w = gray.shape
    out = np.empty((h * 2, w * 2), np.uint8)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            y0, x0 = max(0, y - pad), max(0, x - pad)
            y1, x1 = min(h, y + tile + pad), min(w, x + tile + pad)
            t = cv2.cvtColor(gray[y0:y1, x0:x1], cv2.COLOR_GRAY2BGR)
            up = cv2.cvtColor(sr.upsample(t), cv2.COLOR_BGR2GRAY)
            th, tw = min(tile, h - y) * 2, min(tile, w - x) * 2
            oy, ox = (y - y0) * 2, (x - x0) * 2
            out[y * 2:y * 2 + th, x * 2:x * 2 + tw] = up[oy:oy + th, ox:ox + tw]
    return out


# ------------------------------------------------------ تنقية محافظة على الأختام
def clean_document_preserve_color(img, strength=1):
    """تنقية محلية محافظة: إزالة ضجيج خفيف على الإضاءة مع إبقاء قنوات اللون.
    لا تحذف مكونات متصلة من الصورة، لذلك تقل احتمالية محو نقاط العربية أو تفاصيل الأختام.
    strength: 0..2.
    """
    strength=int(np.clip(strength,0,2))
    if strength==0:return img.copy()
    if img.ndim==2:
        return cv2.fastNlMeansDenoising(img,None, max(2,strength*3),7,21)
    ycc=cv2.cvtColor(img,cv2.COLOR_BGR2YCrCb)
    y,cr,cb=cv2.split(ycc)
    y=cv2.fastNlMeansDenoising(y,None, max(2,strength*3),7,21)
    # CLAHE خفيف على الإضاءة فقط؛ لا يغيّر ألوان الحبر والأختام مباشرةً.
    clahe=cv2.createCLAHE(clipLimit=1.25+0.25*strength,tileGridSize=(8,8))
    y=clahe.apply(y)
    out=cv2.cvtColor(cv2.merge((y,cr,cb)),cv2.COLOR_YCrCb2BGR)
    return _unsharp(out,0.18,1.0)


# -------------------------------------------------------------- الفلاتر
def _lut(lo, hi, gamma=1.0):
    x = np.arange(256, dtype=np.float32)
    y = np.clip((x - lo) / float(hi - lo), 0, 1) ** gamma
    return (y * 255).astype(np.uint8)


def _unsharp(img, amount=0.6, sigma=1.2):
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)


def _white_point(white):
    return int(250 - 0.5 * white)           # white: 0..100


def _defringe(img):
    """إزالة حواف الألوان الرفيعة (أحمر/أخضر) حول الخطوط السوداء.

    السبب: مستشعرات الماسح الملوّن غير متطابقة registration، فتنشأ شوائب
    ملوّنة على جانبي الخطوط الرفيعة (خصوصًا حدود جداول الفواتير).

    الطريقة: نبحث عن بكسلات ذات تشبّع عالٍ (شوائب معزولة) مجاورة لمناطق
    داكنة (الخط الأسود نفسه). هذه الشوائب تُخفَّض تشبّعها فقط، بينما تبقى
    الألوان الحقيقية الكبيرة (الأختام والشعارات والنص الملوّن) كما هي.
    """
    try:
        ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        y, cr, cb = cv2.split(ycc)
        cr_i = cr.astype(np.int16)
        cb_i = cb.astype(np.int16)
        chroma = np.maximum(np.abs(cr_i - 128), np.abs(cb_i - 128)).astype(np.uint8)
        local_mean = cv2.blur(chroma, (7, 7))
        dark_near = cv2.blur((y < 110).astype(np.float32), (7, 7))
        spike = (chroma > 20) & (local_mean < chroma * 0.70) & (dark_near > 0.05)
        if not spike.any():
            return img
        scale = np.ones(chroma.shape, np.float32)
        scale[spike] = np.maximum(0.08, local_mean[spike] / np.maximum(chroma[spike], 1))
        cr2 = np.clip(128 + (cr.astype(np.float32) - 128) * scale, 0, 255).astype(np.uint8)
        cb2 = np.clip(128 + (cb.astype(np.float32) - 128) * scale, 0, 255).astype(np.uint8)
        return cv2.cvtColor(cv2.merge((y, cr2, cb2)), cv2.COLOR_YCrCb2BGR)
    except Exception:
        return img


def _magic_color(img, ink, white, defringe=True):
    # إزالة شوائب الألوان *قبل* توحيد التباين، لأن التوحيد نفس�� يزيد إشباعها.
    if defringe:
        img = _defringe(img)
    img = cv2.LUT(img, _lut(25, _white_point(white), 1.12 + 0.08 * ink))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # تشبّع أعلى في المناطق الفاتحة (الصور)، وأقل في المناطق الداكنة (الأسود
    # والحبر) حتى تبقى حدود الجداول سوداء ناصعة بلا شوائب حمراء/خضراء.
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)
    factor = 0.30 + 0.95 * np.clip((val - 70.0) / 90.0, 0.0, 1.0)
    hsv[:, :, 1] = np.clip(sat * factor, 0, 255).astype(np.uint8)
    img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return _unsharp(img, 0.8, 1.0)


def _clean_gray(g, ink, white):
    g = cv2.LUT(g, _lut(20, _white_point(white), 1.1 + 0.08 * ink))
    return _unsharp(g, 0.7, 1.0)


def _sauvola_2x(g1, g2, dpi, ink):
    """عتبة Sauvola المحلية، تُطبَّق بدقة مضاعفة (شرائح لتوفير الذاكرة).
    g1: رمادي بالدقة الأصلية، g2: نسخة 2× (من الذكاء الاصطناعي) أو None."""
    h, w = g1.shape
    win = int(0.17 * dpi) | 1
    k = float(np.clip(0.20 - 0.04 * ink, 0.05, 0.40))
    mean = cv2.boxFilter(g1, cv2.CV_32F, (win, win), borderType=cv2.BORDER_REPLICATE)
    sq = cv2.sqrBoxFilter(g1, cv2.CV_32F, (win, win), borderType=cv2.BORDER_REPLICATE)
    std = np.sqrt(np.maximum(sq - mean * mean, 0))
    thr = mean * (1.0 + k * (std / 128.0 - 1.0))
    del sq, std
    out = np.empty((h * 2, w * 2), np.uint8)
    step, m = 400, 4
    for y in range(0, h, step):
        y0, y1 = max(0, y - m), min(h, y + step + m)
        if g2 is not None:
            gu = g2[y0 * 2:y1 * 2]
        else:
            gu = cv2.resize(g1[y0:y1], (w * 2, (y1 - y0) * 2), interpolation=cv2.INTER_CUBIC)
        tu = cv2.resize(thr[y0:y1], (w * 2, (y1 - y0) * 2), interpolation=cv2.INTER_LINEAR)
        b = np.where(gu > tu, 255, 0).astype(np.uint8)
        a, z = (y - y0) * 2, (y - y0) * 2 + (min(h, y + step) - y) * 2
        out[y * 2:min(h, y + step) * 2] = b[a:z]
    return out


def _clean_bw(g1, g2, dpi, ink, dust=True):
    h, w = g1.shape
    if h * w > 12_000_000:                   # صفحات ضخمة: معالجة أخف لحماية الذاكرة
        g1 = cv2.GaussianBlur(g1, (0, 0), 0.6)
        g2 = None
    bw2 = _sauvola_2x(g1, g2, dpi, ink)
    if not dust:
        return bw2
    # إزالة الغبار النقطي فقط. الحد محسوب من الدقة ليكون أصغر بكثير من مساحة
    # نقطة الحرف العربية/الإنجليزية حتى لا تُمحى النقاط المنفصلة.
    # (يمكن تعطيله كليًا من الواجهة عبر خيار «إزالة الغبار».)
    min_area = max(6, int(round(12 * (dpi / 300.0) ** 2)))
    inv = 255 - bw2
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    if n > 1:
        remove = np.zeros(n, dtype=bool)
        remove[1:] = stats[1:, cv2.CC_STAT_AREA] < min_area
        bw2[remove[labels]] = 255
    del labels, inv
    return bw2


# --------------------------------------------------------------- الواجهة
def process(img_bgr, mode="magic", crop=True, deskew=True, shadows=True,
            denoise=False, dpi=300, ai=False, ink=0, white=50, trim=True,
            dust=True, defringe=True):
    """يعيد (PIL.Image, زاوية_التصحيح, معامل_التكبير, رسالة_اختيارية).

    ملاحظة مهمة: ترتيب الخطوات مقصود.
      1) قص حدود الورقة (اختياري)
      2) إزالة الحواف السوداء (trim) — يجب أن تجري *قبل* تصحيح الميلان،
         وإلا بقي إطار الماسح الأسود في الصورة وأفسد ملف الإسقاط فلم
         يُكتشف الميلان إطلاقًا (الميلان التلقائي كان يفشل صامتًا).
      3) إزالة الظلال
      4) تصحيح الميلان
      5) التنقية والفلتر النهائي
    """
    note = ""
    img = img_bgr
    if crop:
        img = crop_page(img)
    if trim:
        img = trim_dark_edges(img)
    if shadows and mode != "original":
        img = flatten_illumination(img, passes=2)
    angle = 0.0
    if deskew:
        angle = estimate_skew(img)
        if angle != 0.0:
            img = rotate_keep(img, angle)
    if denoise and mode != "original":
        # التنقية متعددة المستويات من scikit-image اختيارية، وتعمل على الإضاءة فقط
        # لتقليل الضجيج مع المحافظة على ألوان الأختام. نعود إلى OpenCV عند غيابها.
        try:
            from skimage.restoration import denoise_wavelet
            ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
            y, cr, cb = cv2.split(ycc)
            yf = y.astype(np.float32) / 255.0
            clean = denoise_wavelet(
                yf, method="BayesShrink", mode="soft", wavelet="db2",
                rescale_sigma=True, channel_axis=None
            )
            y2 = np.clip(clean * 255.0, 0, 255).astype(np.uint8)
            img = cv2.cvtColor(cv2.merge((y2, cr, cb)), cv2.COLOR_YCrCb2BGR)
        except Exception:
            img = cv2.fastNlMeansDenoisingColored(img, None, 3, 3, 7, 15)

    sr = None
    if ai and mode != "original":
        try:
            if max(img.shape[:2]) > 4300:
                note = "الذكاء الاصطناعي يعمل حتى 300 DPI تقريبًا؛ تم تخطيه لهذه الصفحة."
            else:
                sr = get_sr()
        except Exception as e:                # لا نُسقط المعالجة كلها
            note = "تعذر تشغيل الذكاء الاصطناعي: %s" % e
    scale = 1

    if mode == "bw":
        g1 = _to_gray(img)
        g2 = ai_upscale_gray(g1, sr) if sr is not None else None
        bw2 = _clean_bw(g1, g2, dpi, ink, dust)
        if sr is not None:
            out = cv2.LUT(cv2.GaussianBlur(bw2, (0, 0), 0.7), _lut(70, 190, 1.0))
            scale = 2
        else:
            small = cv2.resize(bw2, (g1.shape[1], g1.shape[0]), interpolation=cv2.INTER_AREA)
            out = cv2.LUT(small, _lut(60, 200, 1.0))   # حواف ناعمة وحادة
    elif mode == "gray":
        g = _to_gray(img)
        if sr is not None:
            g = ai_upscale_gray(g, sr); scale = 2
        out = _clean_gray(g, ink, white)
    elif mode == "magic":
        if sr is not None:
            ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
            y = ai_upscale_gray(ycc[:, :, 0], sr)
            cr = cv2.resize(ycc[:, :, 1], (y.shape[1], y.shape[0]), interpolation=cv2.INTER_CUBIC)
            cb = cv2.resize(ycc[:, :, 2], (y.shape[1], y.shape[0]), interpolation=cv2.INTER_CUBIC)
            img = cv2.cvtColor(cv2.merge([y, cr, cb]), cv2.COLOR_YCrCb2BGR)
            scale = 2
        out = _magic_color(img, ink, white, defringe)
    else:
        out = img
    return bgr_to_pil(out), angle, scale, note
