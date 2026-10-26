# -*- coding: utf-8 -*-
"""أيقونة Canon Scan Pro — تُرسم برمجيًا فلا تحتاج ملفات صور خارجية.

التصميم: ورقة مستند بين أقواس التصوير (viewfinder) يمر عبرها شعاع ماسح ضوئي متوهج؛
الجزء الذي مرّ عليه الشعاع يظهر بنصوص زرقاء «ممسوحة» والجزء الذي لم يُمسح بعد رمادي باهت.
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFilter


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_icon(size=256):
    """يرجع صورة RGBA مربعة بالحجم المطلوب."""
    S = max(size * 4, 512)          # رسم عالي الدقة ثم تصغير ناعم
    u = S / 100.0                    # وحدة قياس: 1% من الضلع
    small = size <= 40               # نسخة مبسطة للأحجام الصغيرة

    # ---------- الخلفية: مربع مستدير بتدرج أزرق ----------
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bot = (38, 118, 255), (9, 38, 140)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=_lerp(top, bot, y / (S - 1)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(22 * u), fill=255)
    bg.paste(grad, (0, 0), mask)
    img = bg

    # ---------- ظل الورقة ----------
    px0, py0, px1, py1 = 25 * u, 15 * u, 75 * u, 85 * u
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [px0 + 1.2 * u, py0 + 2.4 * u, px1 + 1.2 * u, py1 + 2.4 * u],
        radius=int(4 * u), fill=(0, 10, 60, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(2.2 * u))
    img = Image.alpha_composite(img, shadow)

    # ---------- الورقة ----------
    page = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pd = ImageDraw.Draw(page)
    fold = 13 * u
    pd.polygon([(px0 + 4 * u, py0), (px1 - fold, py0), (px1, py0 + fold), (px1, py1 - 4 * u),
                (px1 - 4 * u, py1), (px0 + 4 * u, py1), (px0, py1 - 4 * u), (px0, py0 + 4 * u)],
               fill=(255, 255, 255, 255))
    for (cx, cy) in ((px0 + 4 * u, py0 + 4 * u), (px1 - 4 * u, py1 - 4 * u), (px0 + 4 * u, py1 - 4 * u)):
        pd.ellipse([cx - 4 * u, cy - 4 * u, cx + 4 * u, cy + 4 * u], fill=(255, 255, 255, 255))
    # زاوية الورقة المطوية
    pd.polygon([(px1 - fold, py0), (px1, py0 + fold), (px1 - fold, py0 + fold)], fill=(205, 220, 250, 255))
    img = Image.alpha_composite(img, page)

    beam_y = 56 * u                   # موضع الشعاع
    lx0, lx1 = px0 + 6 * u, px1 - 6 * u

    # ---------- خطوط النص ----------
    if not small:
        d = ImageDraw.Draw(img)
        lw = max(2, int(2.6 * u))
        rows = [(26, 0.78), (33, 1.0), (40, 0.62), (47, 0.9), (54, 0.55),
                (62, 1.0), (69, 0.72), (76, 0.85)]
        for ry, frac in rows:
            y = ry * u
            x_end = lx0 + (lx1 - lx0) * frac
            if ry == 26:                          # سطر العنوان أثقل
                x_end = lx0 + (lx1 - lx0) * 0.55
            col = (30, 98, 235, 255) if y < beam_y else (196, 204, 220, 255)
            width = int(lw * 1.5) if ry == 26 else lw
            d.rounded_rectangle([lx0, y - width / 2, x_end, y + width / 2], radius=width // 2, fill=col)
    else:
        d = ImageDraw.Draw(img)
        lw = max(1, int(3.2 * u))
        for ry in (30, 41, 66, 77):
            y = ry * u
            col = (30, 98, 235, 255) if y < beam_y else (196, 204, 220, 255)
            d.rounded_rectangle([lx0, y - lw / 2, lx1, y + lw / 2], radius=lw // 2, fill=col)

    # ---------- شعاع المسح المتوهج ----------
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle([14 * u, beam_y - 3.2 * u, 86 * u, beam_y + 3.2 * u],
                         radius=int(3 * u), fill=(0, 235, 255, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(3.4 * u))
    img = Image.alpha_composite(img, glow)
    # توهج على الورقة نفسها أسفل الشعاع (أثر الضوء)
    wash = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wash)
    steps = 14
    for i in range(steps):
        a = int(70 * (1 - i / steps) ** 2)
        y0 = beam_y + i * 0.5 * u
        wd.rectangle([px0 + 1, y0, px1 - 1, y0 + 0.55 * u], fill=(0, 220, 255, a))
    pm = Image.new("L", (S, S), 0)
    ImageDraw.Draw(pm).rounded_rectangle([px0, py0, px1, py1], radius=int(4 * u), fill=255)
    wash.putalpha(Image.composite(wash.getchannel("A"), Image.new("L", (S, S), 0), pm))
    img = Image.alpha_composite(img, wash)

    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    cd.rounded_rectangle([16 * u, beam_y - 1.35 * u, 84 * u, beam_y + 1.35 * u],
                         radius=int(1.4 * u), fill=(215, 255, 255, 255))
    img = Image.alpha_composite(img, core)

    # ---------- أقواس التصوير في الزوايا ----------
    d = ImageDraw.Draw(img)
    bw = max(2, int(2.8 * u))
    arm = 10 * u
    m = 9 * u                      # ابتعاد القوس عن حافة الأيقونة
    bc = (255, 255, 255, 255)
    for (cx, cy, sx, sy) in ((m, m, 1, 1), (S - m, m, -1, 1), (m, S - m, 1, -1), (S - m, S - m, -1, -1)):
        d.line([(cx, cy + sy * arm), (cx, cy), (cx + sx * arm, cy)], fill=bc, width=bw, joint="curve")
        for (ex, ey) in ((cx, cy + sy * arm), (cx + sx * arm, cy)):
            d.ellipse([ex - bw / 2, ey - bw / 2, ex + bw / 2, ey + bw / 2], fill=bc)
        d.ellipse([cx - bw / 2, cy - bw / 2, cx + bw / 2, cy + bw / 2], fill=bc)

    return img.resize((size, size), Image.LANCZOS)


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def save_ico(path):
    """يحفظ ملف .ico متعدد الأحجام (لأيقونة EXE والمثبّت)."""
    base = make_icon(256)
    frames = [make_icon(s) for s in ICO_SIZES]
    base.save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=frames)
    return path


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def apply_window_icon(root):
    """يضبط أيقونة النافذة وشريط المهام دون الاعتماد على أي ملف خارجي."""
    try:
        from PIL import ImageTk
        photos = [ImageTk.PhotoImage(make_icon(s)) for s in (256, 64, 32)]
        root.iconphoto(True, *photos)
        root._icon_refs = photos          # منع جمع القمامة
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            ico = resource_path(os.path.join("assets", "CanonScanPro.ico"))
            if os.path.exists(ico):
                root.iconbitmap(default=ico)
        except Exception:
            pass


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    os.makedirs(out, exist_ok=True)
    print("saved", save_ico(os.path.join(out, "CanonScanPro.ico")))
    make_icon(512).save(os.path.join(out, "CanonScanPro.png"))
