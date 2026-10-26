# -*- coding: utf-8 -*-
"""
ui_theme.py - نظام تصميم احترافي لبرنامج Canon Scan Pro (Tkinter)
ثيم فاتح وداكن، خطوط واضحة، أزرار مستديرة ناعمة (Anti-aliased)، مفاتيح تبديل،
منزلقات مخصصة، بطاقات، وأيقونات مرسومة برمجيًا (لا تعتمد على خطوط أيقونات).
"""
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageDraw, ImageTk

# ------------------------------------------------------------------ الألوان
THEMES = {
    "light": dict(
        bg="#F3F5F9", surface="#FFFFFF", surface2="#F7F9FC", sunken="#EBEFF5",
        border="#DDE3EC", border_strong="#C6CFDC",
        fg="#162033", fg2="#4A586E", muted="#8392A8",
        accent="#2563EB", accent_h="#1D4FD0", accent_p="#1A45B8", accent_fg="#FFFFFF",
        accent_soft="#E6EEFF", accent_soft_fg="#1E4FC4",
        ok="#12805C", warn="#B7791F", danger="#D92D3A", danger_soft="#FDECEE",
        canvas="#DCE3EE", paper_shadow="#6F7F99", track="#D5DCE8", hover="#EDF1F7", warn_fg="#FFFFFF",
    ),
    "dark": dict(
        bg="#0F131A", surface="#171C25", surface2="#1C222D", sunken="#11161D",
        border="#262E3B", border_strong="#344052",
        fg="#E8ECF3", fg2="#B1BBCB", muted="#7C889B",
        accent="#4C8DFF", accent_h="#6AA0FF", accent_p="#3B7AEB", accent_fg="#06122B",
        accent_soft="#1B2A47", accent_soft_fg="#8DB6FF",
        ok="#3DD598", warn="#F2B35E", danger="#FF6B76", danger_soft="#3A1D22",
        canvas="#0B0E13", paper_shadow="#000000", track="#2B3442", hover="#212835", warn_fg="#2A1B00",
    ),
}
T = dict(THEMES["light"])
THEME_NAME = "light"
_listeners = []


def set_theme(name):
    global THEME_NAME
    THEME_NAME = name if name in THEMES else "light"
    T.clear()
    T.update(THEMES[THEME_NAME])


def register(widget):
    """سجّل ويدجت ليُعاد رسمه عند تبديل الثيم (يحتاج دالة retheme)."""
    _listeners.append(widget)


def retheme_all():
    alive = []
    for w in _listeners:
        try:
            if w.winfo_exists():
                w.retheme()
                alive.append(w)
        except Exception:
            pass
    _listeners[:] = alive


# ------------------------------------------------------------------- الخطوط
_FONTS = {}


def _pick(root, candidates):
    fams = set(tkfont.families(root))
    for c in candidates:
        if c in fams:
            return c
    return "TkDefaultFont"


def init_fonts(root):
    fam = _pick(root, ["Segoe UI Variable Text", "Segoe UI", "Tahoma", "DejaVu Sans"])
    disp = _pick(root, ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", fam])
    _FONTS.update(
        body=(fam, 10), body_b=(fam, 10, "bold"), small=(fam, 9), small_b=(fam, 9, "bold"),
        tiny=(fam, 8), h1=(disp, 17, "bold"), h2=(disp, 12, "bold"), h3=(fam, 10, "bold"),
        btn=(fam, 10, "bold"), big=(disp, 20, "bold"), mono=("Consolas", 9),
    )
    return _FONTS


def F(name):
    return _FONTS.get(name, ("TkDefaultFont", 10))


# ------------------------------------------------------- رسم الأشكال بنعومة
_CACHE = {}
_CACHE_MAX = 400          # سقف لعدد العناصر المخزّنة (منع تسريب الذاكرة)
_SS = 4  # supersampling


_PINNED = {}   # الأيقونات: مجموعة صغيرة ومحدودة، ولا تُفرَّغ أبدًا (تبقى حيّة لأن عناصر Canvas لا تحتفظ بمرجع لها)


def _cache_put(key, value):
    """تخزين مؤقت مع حدّ أقصى — عند تجاوزه يُفرَّغ الكاش مع إبقاء الأيقونات."""
    if key and key[0] == "ic":
        _PINNED[key] = value
        return value
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = value
    return value


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def rr_image(w, h, r, fill, outline=None, bw=1):
    """مستطيل مستدير بحواف ناعمة مع شفافية."""
    key = ("rr", w, h, r, fill, outline, bw)
    if key in _CACHE:
        return _CACHE[key]
    w, h = max(2, int(w)), max(2, int(h))
    im = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    box = [0, 0, w * _SS - 1, h * _SS - 1]
    if outline:
        d.rounded_rectangle(box, radius=r * _SS, fill=_hex(outline))
        inset = bw * _SS
        d.rounded_rectangle([inset, inset, w * _SS - 1 - inset, h * _SS - 1 - inset],
                            radius=max(0, (r - bw)) * _SS, fill=_hex(fill) if fill else (0, 0, 0, 0))
    elif fill:
        d.rounded_rectangle(box, radius=r * _SS, fill=_hex(fill))
    im = im.resize((w, h), Image.LANCZOS)
    ph = ImageTk.PhotoImage(im)
    return _cache_put(key, ph)


# ----------------------------------------------------------------- الأيقونات
def _line(d, pts, w, col):
    d.line(pts, fill=col, width=w, joint="curve")
    r = w / 2
    for (x, y) in (pts[0], pts[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)


def icon_image(name, size, color):
    key = ("ic", name, size, color)
    if key in _PINNED:
        return _PINNED[key]
    S = size * 8
    k = S / 24.0
    col = _hex(color)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = int(2 * k)

    def P(*pts):
        return [(x * k, y * k) for x, y in pts]

    def arc(cx, cy, r, a0, a1):
        d.arc([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], a0, a1, fill=col, width=w)

    def rect(x1, y1, x2, y2, r=2):
        d.rounded_rectangle([x1 * k, y1 * k, x2 * k, y2 * k], radius=r * k, outline=col, width=w)

    if name == "scan":
        rect(3, 9, 21, 19, 2.5)
        _line(d, P((2, 12.5), (22, 12.5)), w, col)
        _line(d, P((7, 5), (17, 5)), w, col)
        _line(d, P((7, 5), (7, 9)), w, col)
        _line(d, P((17, 5), (17, 9)), w, col)
    elif name == "image":
        rect(3, 4, 21, 20, 3)
        d.ellipse([7.5 * k, 8 * k, 11 * k, 11.5 * k], outline=col, width=int(1.6 * k))
        _line(d, P((4, 17), (9.5, 12.5), (13, 16), (16, 13), (20, 17)), w, col)
    elif name == "folder":
        _line(d, P((3, 7), (3, 19), (21, 19), (21, 9), (11, 9), (9, 6), (3, 6), (3, 7)), w, col)
    elif name == "save":
        rect(4, 4, 20, 20, 3)
        _line(d, P((8, 4), (8, 9), (15, 9), (15, 4)), w, col)
        _line(d, P((8, 20), (8, 14), (16, 14), (16, 20)), w, col)
    elif name == "pdf":
        _line(d, P((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), (6, 3)), w, col)
        _line(d, P((14, 3), (14, 8), (19, 8)), w, col)
        _line(d, P((9.5, 13), (15.5, 13)), w, col)
        _line(d, P((9.5, 16.5), (15.5, 16.5)), w, col)
    elif name == "text":
        _line(d, P((5, 6), (19, 6)), w, col)
        _line(d, P((12, 6), (12, 19)), w, col)
        _line(d, P((9, 19), (15, 19)), w, col)
    elif name == "search_pdf":
        _line(d, P((5, 3), (12, 3), (16, 7), (16, 14)), w, col)
        _line(d, P((5, 3), (5, 21), (11, 21)), w, col)
        d.ellipse([11 * k, 13 * k, 18.5 * k, 20.5 * k], outline=col, width=w)
        _line(d, P((18, 20), (21, 23)), w, col)
    elif name == "trash":
        _line(d, P((4, 7), (20, 7)), w, col)
        _line(d, P((9, 7), (9, 4), (15, 4), (15, 7)), w, col)
        _line(d, P((6, 7), (7, 20), (17, 20), (18, 7)), w, col)
        _line(d, P((10.5, 11), (10.5, 16)), w, col)
        _line(d, P((13.5, 11), (13.5, 16)), w, col)
    elif name == "clear":
        _line(d, P((6, 6), (18, 18)), w, col)
        _line(d, P((18, 6), (6, 18)), w, col)
    elif name == "undo":
        _line(d, P((9, 6), (4, 11), (9, 16)), w, col)
        _line(d, P((4, 11), (14, 11), (19, 13), (20, 18)), w, col)
    elif name == "redo":
        _line(d, P((15, 6), (20, 11), (15, 16)), w, col)
        _line(d, P((20, 11), (10, 11), (5, 13), (4, 18)), w, col)
    elif name == "crop":
        _line(d, P((7, 2), (7, 17), (22, 17)), w, col)
        _line(d, P((2, 7), (17, 7), (17, 22)), w, col)
    elif name == "eye":
        _line(d, P((2, 12), (6, 7.5), (12, 5.5), (18, 7.5), (22, 12), (18, 16.5), (12, 18.5), (6, 16.5), (2, 12)), w, col)
        d.ellipse([9 * k, 9 * k, 15 * k, 15 * k], outline=col, width=w)
    elif name == "compare":
        rect(3, 4, 21, 20, 3)
        _line(d, P((12, 4), (12, 20)), w, col)
        _line(d, P((7, 12), (9, 12)), w, col)
        _line(d, P((15, 12), (17, 12)), w, col)
    elif name == "fit":
        for a, b, c in (((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)),
                        ((20, 15), (20, 20), (15, 20)), ((9, 20), (4, 20), (4, 15))):
            _line(d, P(a, b, c), w, col)
    elif name == "plus":
        _line(d, P((12, 5), (12, 19)), w, col)
        _line(d, P((5, 12), (19, 12)), w, col)
    elif name == "minus":
        _line(d, P((5, 12), (19, 12)), w, col)
    elif name == "sun":
        d.ellipse([7 * k, 7 * k, 17 * k, 17 * k], outline=col, width=w)
        import math
        for i in range(8):
            a = math.radians(i * 45)
            _line(d, P((12 + 8 * math.cos(a), 12 + 8 * math.sin(a)),
                       (12 + 10.5 * math.cos(a), 12 + 10.5 * math.sin(a))), w, col)
    elif name == "moon":
        from PIL import ImageChops
        m1 = Image.new("L", (S, S), 0)
        m2 = Image.new("L", (S, S), 0)
        ImageDraw.Draw(m1).ellipse([3.5 * k, 3.5 * k, 20.5 * k, 20.5 * k], fill=255)
        ImageDraw.Draw(m2).ellipse([8.5 * k, 0.5 * k, 25.5 * k, 17.5 * k], fill=255)
        mask = ImageChops.subtract(m1, m2)
        im = Image.new("RGBA", (S, S), col + (0,))
        im.putalpha(mask)
    elif name == "prev":
        _line(d, P((9, 5), (16, 12), (9, 19)), w, col)
    elif name == "next":
        _line(d, P((15, 5), (8, 12), (15, 19)), w, col)
    elif name == "wand":
        _line(d, P((4, 20), (15, 9)), int(2.6 * k), col)
        _line(d, P((17, 3), (17, 8)), w, col)
        _line(d, P((14.5, 5.5), (19.5, 5.5)), w, col)
        _line(d, P((20, 12), (20, 15)), w, col)
        _line(d, P((18.5, 13.5), (21.5, 13.5)), w, col)
    elif name == "check":
        _line(d, P((5, 12.5), (10, 17.5), (19, 7)), int(2.4 * k), col)
    elif name == "sliders":
        _line(d, P((4, 8), (20, 8)), w, col)
        _line(d, P((4, 16), (20, 16)), w, col)
        d.ellipse([7 * k, 5.5 * k, 12 * k, 10.5 * k], fill=col)
        d.ellipse([13 * k, 13.5 * k, 18 * k, 18.5 * k], fill=col)
    elif name == "doc":
        _line(d, P((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), (6, 3)), w, col)
        _line(d, P((14, 3), (14, 8), (19, 8)), w, col)
    elif name == "layers":
        _line(d, P((12, 3), (21, 8), (12, 13), (3, 8), (12, 3)), w, col)
        _line(d, P((3, 12), (12, 17), (21, 12)), w, col)
        _line(d, P((3, 16), (12, 21), (21, 16)), w, col)
    elif name == "export":
        _line(d, P((12, 15), (12, 3)), w, col)
        _line(d, P((7, 8), (12, 3), (17, 8)), w, col)
        _line(d, P((4, 14), (4, 20), (20, 20), (20, 14)), w, col)
    elif name in ("rotate", "rot_right", "rot_left"):
        arc(12, 12, 8, 30, 320)
        _line(d, P((18, 3), (18, 8), (13, 8)), w, col)
        if name == "rot_left":
            from PIL import ImageOps
            im = ImageOps.mirror(im)
    im = im.resize((size, size), Image.LANCZOS)
    ph = ImageTk.PhotoImage(im)
    return _cache_put(key, ph)


# ===================================================================== الأزرار
_FIT_FONTS = {}


class Btn(tk.Canvas):
    """زر مستدير بحواف ناعمة. variant: primary | secondary | ghost | danger | soft"""

    def __init__(self, master, text="", command=None, icon=None, variant="secondary",
                 height=38, radius=9, font="btn", pad=14, fill_x=True, bg_token="surface", tip=None):
        super().__init__(master, height=height, width=1, highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.command, self.icon, self.variant = text, command, icon, variant
        self.h, self.r, self.fnt, self.pad, self.bg_token = height, radius, font, pad, bg_token
        self.state, self._hover, self._press = "normal", False, False
        self._ims = []
        self._font = tkfont.Font(font=F(font))
        self._min_w = self._font.measure(text) + pad * 2 + (26 if icon else 0)
        if not fill_x:
            self.configure(width=self._min_w)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, press=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(press=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.tip = tip
        if tip:
            Tooltip(self, tip)
        register(self)

    def retheme(self):
        self._draw()

    def _set(self, hover=None, press=None):
        if hover is not None:
            self._hover = hover
        if press is not None:
            self._press = press
        self._draw()

    def _release(self, e):
        was = self._press
        self._press = False
        self._draw()
        if was and self.state != "disabled" and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            if self.command:
                self.command()

    def set_state(self, state):
        self.state = state
        self.configure(cursor="arrow" if state == "disabled" else "hand2")
        self._draw()

    def set_text(self, text):
        self.text = text
        self._draw()

    def set_icon(self, icon):
        self.icon = icon
        self._draw()

    def _colors(self):
        v, st = self.variant, self.state
        if st == "disabled":
            if v == "ghost":
                return None, None, T["border_strong"]
            return T["sunken"], T["border"], T["muted"]
        hv, pr = self._hover, self._press
        if v == "primary":
            f = T["accent_p"] if pr else T["accent_h"] if hv else T["accent"]
            return f, None, T["accent_fg"]
        if v == "danger":
            f = T["danger_soft"] if not hv else T["danger"]
            return f, T["danger"], (T["danger"] if not hv else "#FFFFFF")
        if v == "soft":
            f = T["accent_soft"]
            return (T["accent"] if hv else f), None, (T["accent_fg"] if hv else T["accent_soft_fg"])
        if v == "ghost":
            return (T["hover"] if hv else T["bg"] if False else None), None, T["fg2"] if not hv else T["fg"]
        f = T["hover"] if hv else T["surface2"]
        if pr:
            f = T["sunken"]
        return f, T["border_strong"] if hv else T["border"], T["fg"]

    def _draw(self):
        self.delete("all")
        try:
            self.configure(bg=T[self.bg_token])
        except Exception:
            pass
        w = self.winfo_width()
        if w < 8:
            return
        h = self.h
        fill, outline, fg = self._colors()
        if fill or outline:
            self._bg_im = rr_image(w, h, self.r, fill, outline)
            self.create_image(0, 0, image=self._bg_im, anchor="nw")
        cx, cy = w // 2, h // 2
        # ملاءمة تلقائية: إن ضاق الزر صغّر الخط تدريجيًا ثم أخفِ الأيقونة، فلا يُقصّ النص أبدًا
        fnt, fobj, show_icon = F(self.fnt), self._font, bool(self.icon)
        if self.text:
            avail = w - 12
            def need(fo, ic):
                return fo.measure(self.text) + (22 if ic else 0)
            size = fnt[1]
            while need(fobj, show_icon) > avail and size > 8:
                size -= 1
                fnt = (fnt[0], size) + tuple(fnt[2:])
                fobj = _FIT_FONTS.get(fnt) or _FIT_FONTS.setdefault(fnt, tkfont.Font(font=fnt))
            if need(fobj, show_icon) > avail:
                show_icon = False
        if show_icon:
            ic = icon_image(self.icon, 18, fg)
            tw = fobj.measure(self.text) if self.text else 0
            total = tw + (22 if self.text else 0)
            if self.text:
                # RTL: النص يمين الأيقونة
                x0 = cx - total // 2
                self.create_image(x0 + 9, cy, image=ic)
                self.create_text(x0 + 22, cy, text=self.text, fill=fg, font=fnt, anchor="w")
            else:
                self.create_image(cx, cy, image=ic)
        else:
            self.create_text(cx, cy, text=self.text, fill=fg, font=fnt)


class IconBtn(Btn):
    """زر أيقونة مربع (شريط الأدوات)."""

    def __init__(self, master, icon, command=None, tip=None, size=36, bg_token="bg", variant="ghost"):
        super().__init__(master, text="", command=command, icon=icon, variant=variant,
                         height=size, radius=9, fill_x=False, bg_token=bg_token, tip=tip)
        self.configure(width=size)


# ================================================================== تلميحات
class Tooltip:
    def __init__(self, widget, text):
        self.w, self.text, self.tw = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tw or not self.text:
            return
        x = self.w.winfo_rootx() + self.w.winfo_width() // 2
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tw = tk.Toplevel(self.w)
        self.tw.wm_overrideredirect(True)
        self.tw.attributes("-topmost", True)
        lbl = tk.Label(self.tw, text=self.text, bg=T["fg"], fg=T["surface"], font=F("small"), padx=8, pady=4)
        lbl.pack()
        self.tw.update_idletasks()
        self.tw.wm_geometry(f"+{x - self.tw.winfo_width() // 2}+{y}")

    def _hide(self, _e=None):
        if self.tw:
            self.tw.destroy()
            self.tw = None


# =================================================================== مفتاح تبديل
class Switch(tk.Canvas):
    """مفتاح تبديل (Toggle) مرتبط بـ BooleanVar. النص يمين المفتاح (RTL)."""

    W, H = 40, 22

    def __init__(self, master, text, variable, command=None, bg_token="surface", sub=None):
        super().__init__(master, height=34 if not sub else 46, width=1, highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.text, self.command, self.bg_token, self.sub = variable, text, command, bg_token, sub
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.var.trace_add("write", lambda *a: self._draw())
        register(self)

    def retheme(self):
        self._draw()

    def _h(self, v):
        self._hover = v
        self._draw()

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        self.configure(bg=T[self.bg_token])
        w = self.winfo_width()
        if w < 20:
            return
        h = int(self["height"])
        on = bool(self.var.get())
        track = T["accent"] if on else (T["border_strong"] if self._hover else T["track"])
        self._tr = rr_image(self.W, self.H, self.H // 2, track)
        # المفتاح على اليمين، والنص على يساره مع محاذاة لليمين
        sx = w - self.W - 2
        sy = (h - self.H) // 2
        self.create_image(sx, sy, image=self._tr, anchor="nw")
        kx = sx + (self.W - self.H + 3 if on else 3)
        self.create_oval(kx, sy + 3, kx + self.H - 6, sy + self.H - 3, fill="#FFFFFF", outline="")
        ty = h // 2 - (7 if self.sub else 0)
        self.create_text(sx - 12, ty, text=self.text, fill=T["fg"], font=F("body"), anchor="e")
        if self.sub:
            self.create_text(sx - 12, ty + 17, text=self.sub, fill=T["muted"], font=F("small"), anchor="e")


# ===================================================================== منزلق
class Slider(tk.Canvas):
    """منزلق مخصص مع قيمة معروضة. متوافق مع DoubleVar."""

    def __init__(self, master, label, variable, lo, hi, fmt="{:.0f}", command=None, bg_token="surface", default=None):
        super().__init__(master, height=56, width=1, highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.lo, self.hi, self.fmt, self.command = variable, lo, hi, fmt, command
        self.label, self.bg_token = label, bg_token
        self.default = default if default is not None else variable.get()
        self._drag = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._press)
        self.bind("<ButtonRelease-1>", self._rel)
        self.bind("<Double-Button-1>", self._reset)
        self.var.trace_add("write", lambda *a: self._draw())
        register(self)

    def retheme(self):
        self._draw()

    def _rel(self, _e):
        self._drag = False
        self._draw()

    def _reset(self, _e):
        self.var.set(self.default)
        if self.command:
            self.command()

    def _geom(self):
        w = self.winfo_width()
        return 10, w - 10

    def _press(self, e):
        self._drag = True
        x0, x1 = self._geom()
        t = min(1.0, max(0.0, (e.x - x0) / max(1, x1 - x0)))
        # RTL: اليمين = القيمة الأعلى
        t = 1.0 - t
        self.var.set(self.lo + t * (self.hi - self.lo))
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        self.configure(bg=T[self.bg_token])
        w = self.winfo_width()
        if w < 40:
            return
        x0, x1 = self._geom()
        v = float(self.var.get())
        t = (v - self.lo) / (self.hi - self.lo)
        px = x1 - t * (x1 - x0)  # RTL
        cy = 38
        self.create_text(w - 2, 12, text=self.label, fill=T["fg"], font=F("body"), anchor="e")
        self._pill = rr_image(58, 22, 11, T["accent_soft"])
        self.create_image(2, 1, image=self._pill, anchor="nw")
        self.create_text(31, 12, text=self.fmt.format(v), fill=T["accent_soft_fg"], font=F("small_b"))
        self._trk = rr_image(int(x1 - x0), 6, 3, T["track"])
        self.create_image(x0, cy - 3, image=self._trk, anchor="nw")
        fw = int(x1 - px)
        if fw > 2:
            self._fil = rr_image(fw, 6, 3, T["accent"])
            self.create_image(px, cy - 3, image=self._fil, anchor="nw")
        r = 9 if self._drag else 8
        self.create_oval(px - r - 1, cy - r - 1, px + r + 1, cy + r + 1, fill=T["border_strong"], outline="")
        self.create_oval(px - r, cy - r, px + r, cy + r, fill="#FFFFFF", outline=T["accent"], width=2)


# ================================================================ أزرار مقسّمة
class Segmented(tk.Canvas):
    """شريط خيارات مقسّم (Segmented). options = [(value, label)]. الأول على اليمين (RTL)."""

    def __init__(self, master, options, variable=None, command=None, height=36, bg_token="surface", font="small_b"):
        super().__init__(master, height=height, width=1, highlightthickness=0, bd=0, cursor="hand2")
        self.options, self.var, self.command, self.h = options, variable, command, height
        self.bg_token, self.fnt = bg_token, font
        self.value = variable.get() if variable is not None else options[0][0]
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        if variable is not None:
            variable.trace_add("write", lambda *a: self._sync())
        register(self)

    def retheme(self):
        self._draw()

    def _sync(self):
        self.value = self.var.get()
        self._draw()

    def _click(self, e):
        w = self.winfo_width()
        n = len(self.options)
        seg = (w - 8) / n
        idx = int((w - 4 - e.x) // seg)  # RTL
        idx = max(0, min(n - 1, idx))
        val = self.options[idx][0]
        self.value = val
        if self.var is not None:
            self.var.set(val)
        self._draw()
        if self.command:
            self.command(val)

    def _draw(self):
        self.delete("all")
        self.configure(bg=T[self.bg_token])
        w = self.winfo_width()
        if w < 40:
            return
        self._bgim = rr_image(w, self.h, 10, T["sunken"], T["border"])
        self.create_image(0, 0, image=self._bgim, anchor="nw")
        n = len(self.options)
        seg = (w - 8) / n
        for i, (val, label) in enumerate(self.options):
            xr = w - 4 - i * seg
            xl = xr - seg
            on = (val == self.value)
            if on:
                self._sel = rr_image(int(seg), self.h - 8, 7, T["accent"])
                self.create_image(xl, 4, image=self._sel, anchor="nw")
            self.create_text((xl + xr) / 2, self.h // 2, text=str(label),
                             fill=T["accent_fg"] if on else T["fg2"], font=F(self.fnt))


# ======================================================================= شارة
class Chip(tk.Canvas):
    """شريحة اختيار (للإعدادات الجاهزة)."""

    def __init__(self, master, text, command, selected_fn, bg_token="surface"):
        super().__init__(master, height=32, width=1, highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.command, self.sel, self.bg_token = text, command, selected_fn, bg_token
        self._font = tkfont.Font(font=F("small_b"))
        self.configure(width=self._font.measure(text) + 28)
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        register(self)

    def retheme(self):
        self._draw()

    def _h(self, v):
        self._hover = v
        self._draw()

    def _draw(self):
        self.delete("all")
        self.configure(bg=T[self.bg_token])
        w = self.winfo_width()
        if w < 10:
            return
        on = self.sel()
        if on:
            f, o, c = T["accent_soft"], T["accent"], T["accent_soft_fg"]
        else:
            f, o, c = (T["hover"] if self._hover else T["surface2"]), T["border"], T["fg2"]
        self._im = rr_image(w, 32, 16, f, o)
        self.create_image(0, 0, image=self._im, anchor="nw")
        self.create_text(w // 2, 16, text=self.text, fill=c, font=F("small_b"))


# ====================================================================== بطاقة
class Card(tk.Frame):
    """حاوية بحدود مستديرة تقريبية: إطار بحدود 1px مع عنوان اختياري."""

    def __init__(self, master, title=None, icon=None, bg_token="surface"):
        super().__init__(master, highlightthickness=1, bd=0)
        self.bg_token = bg_token
        self.title, self.icon = title, icon
        self.head = None
        if title:
            self.head = tk.Frame(self, bd=0)
            self.head.pack(fill="x", padx=14, pady=(12, 2))
            self.ic = tk.Label(self.head, bd=0)
            self.lbl = tk.Label(self.head, text=title, font=F("h3"), anchor="e")
            if icon:
                self.ic.pack(side="right", padx=(8, 0))
            self.lbl.pack(side="right")
        self.body = tk.Frame(self, bd=0)
        self.body.pack(fill="x", padx=14, pady=(6, 12))
        register(self)
        self.retheme()

    def retheme(self):
        self.configure(bg=T["surface"], highlightbackground=T["border"], highlightcolor=T["border"])
        self.body.configure(bg=T["surface"])
        if self.head:
            self.head.configure(bg=T["surface"])
            self.lbl.configure(bg=T["surface"], fg=T["fg"])
            if self.icon:
                self.ic.configure(bg=T["surface"], image=icon_image(self.icon, 18, T["accent"]))


def label(master, text, font="body", fg="fg", bg="surface", anchor="e", **kw):
    """Label مرتبط بالثيم (يعاد تلوينه تلقائيًا)."""
    l = _TLabel(master, text, font, fg, bg, anchor, **kw)
    return l


class _TLabel(tk.Label):
    def __init__(self, master, text, font, fg, bg, anchor, **kw):
        super().__init__(master, text=text, font=F(font), anchor=anchor, bd=0, justify="right", **kw)
        self._fg, self._bg = fg, bg
        register(self)
        self.retheme()

    def retheme(self):
        self.configure(bg=T[self._bg], fg=T[self._fg])

    def set_tokens(self, fg=None, bg=None):
        if fg:
            self._fg = fg
        if bg:
            self._bg = bg
        self.retheme()


class Themed(tk.Frame):
    """Frame يتبع لون خلفية من الثيم."""

    def __init__(self, master, bg_token="surface", **kw):
        super().__init__(master, bd=0, highlightthickness=0, **kw)
        self.bg_token = bg_token
        register(self)
        self.retheme()

    def retheme(self):
        self.configure(bg=T[self.bg_token])


class HSep(tk.Frame):
    def __init__(self, master, bg_token="border"):
        super().__init__(master, height=1, bd=0)
        self.tok = bg_token
        register(self)
        self.retheme()

    def retheme(self):
        self.configure(bg=T[self.tok])


# ===================================================================== تبويبات
class Tabs(tk.Canvas):
    """تبويبات علوية للوحة اليمنى (أيقونة + نص). الأول على اليمين."""

    def __init__(self, master, items, command, height=64):
        super().__init__(master, height=height, width=1, highlightthickness=0, bd=0, cursor="hand2")
        self.items, self.command, self.h = items, command, height
        self.active = items[0][0]
        self._hover = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._move)
        self.bind("<Leave>", lambda e: self._setover(None))
        register(self)

    def retheme(self):
        self._draw()

    def _idx(self, x):
        w = self.winfo_width()
        n = len(self.items)
        seg = w / n
        return max(0, min(n - 1, int((w - x) // seg)))

    def _move(self, e):
        self._setover(self._idx(e.x))

    def _setover(self, i):
        if i != self._hover:
            self._hover = i
            self._draw()

    def _click(self, e):
        key = self.items[self._idx(e.x)][0]
        self.select(key)

    def select(self, key):
        self.active = key
        self._draw()
        self.command(key)

    def _draw(self):
        self.delete("all")
        self.configure(bg=T["surface"])
        w = self.winfo_width()
        if w < 40:
            return
        n = len(self.items)
        seg = w / n
        self.create_line(0, self.h - 1, w, self.h - 1, fill=T["border"])
        for i, (key, text, icon) in enumerate(self.items):
            xr = w - i * seg
            cx = xr - seg / 2
            on = key == self.active
            col = T["accent"] if on else (T["fg"] if self._hover == i else T["muted"])
            self.create_image(cx, 20, image=icon_image(icon, 20, col))
            self.create_text(cx, 44, text=text, fill=col, font=F("small_b" if on else "small"))
            if on:
                self._ind = rr_image(int(seg - 20), 3, 1, T["accent"])
                self.create_image(xr - seg + 10, self.h - 3, image=self._ind, anchor="nw")


# ===================================================================== تقدّم
class Progress(tk.Canvas):
    def __init__(self, master, bg_token="surface"):
        super().__init__(master, height=4, width=1, highlightthickness=0, bd=0)
        self.bg_token = bg_token
        self.running, self.pos, self._job = False, 0.0, None
        self.bind("<Configure>", lambda e: self._draw())
        register(self)

    def retheme(self):
        self._draw()

    def start(self):
        if self.running:
            return
        self.running = True
        self._tick()

    def stop(self):
        self.running = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._job = None
        self._draw()

    def _tick(self):
        if not self.running:
            return
        self.pos = (self.pos + 0.025) % 1.4
        self._draw()
        self._job = self.after(24, self._tick)

    def _draw(self):
        self.delete("all")
        self.configure(bg=T[self.bg_token])
        w = self.winfo_width()
        if w < 10:
            return
        if self.running:
            self.create_rectangle(0, 0, w, 4, fill=T["track"], outline="")
            seg = w * 0.35
            xr = w - (self.pos - 0.2) * w
            self.create_rectangle(max(0, xr - seg), 0, min(w, xr), 4, fill=T["accent"], outline="")


# ================================================================ قائمة خيارات
class Choice(tk.Canvas):
    """قائمة خيارات رأسية (Radio) بصفوف واضحة. options = [(value, label)]."""

    ROW = 40

    def __init__(self, master, options, variable, command=None, bg_token="surface"):
        super().__init__(master, height=len(options) * self.ROW, width=1, highlightthickness=0, bd=0, cursor="hand2")
        self.options, self.var, self.command, self.bg_token = options, variable, command, bg_token
        self._hover = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", lambda e: self._ho(e.y // self.ROW))
        self.bind("<Leave>", lambda e: self._ho(None))
        variable.trace_add("write", lambda *a: self._draw())
        register(self)

    def retheme(self):
        self._draw()

    def _ho(self, i):
        if i != self._hover:
            self._hover = i
            self._draw()

    def _click(self, e):
        i = max(0, min(len(self.options) - 1, e.y // self.ROW))
        self.var.set(self.options[i][0])
        if self.command:
            self.command(self.options[i][0])

    def _draw(self):
        self.delete("all")
        self.configure(bg=T[self.bg_token])
        w = self.winfo_width()
        if w < 40:
            return
        self._ims = []
        for i, (val, text) in enumerate(self.options):
            y = i * self.ROW
            on = self.var.get() == val
            if on:
                im = rr_image(w, self.ROW - 6, 9, T["accent_soft"], T["accent"])
            elif self._hover == i:
                im = rr_image(w, self.ROW - 6, 9, T["hover"])
            else:
                im = None
            if im:
                self._ims.append(im)
                self.create_image(0, y + 3, image=im, anchor="nw")
            cy = y + self.ROW // 2
            rx = w - 20
            self.create_oval(rx - 8, cy - 8, rx + 8, cy + 8, outline=T["accent"] if on else T["border_strong"],
                             width=2, fill=T["surface"] if not on else T["accent"])
            if on:
                self.create_oval(rx - 3, cy - 3, rx + 3, cy + 3, fill="#FFFFFF", outline="")
            self.create_text(rx - 18, cy, text=text, fill=T["accent_soft_fg"] if on else T["fg"],
                             font=F("body_b" if on else "body"), anchor="e")


# ===================================================================== حقل إدخال
class Field(tk.Frame):
    def __init__(self, master, variable, width=10, justify="right", bg_token="surface"):
        super().__init__(master, highlightthickness=1, bd=0)
        self.bg_token = bg_token
        self.entry = tk.Entry(self, textvariable=variable, font=F("body"), bd=0, relief="flat",
                              justify=justify, width=width, highlightthickness=0)
        self.entry.pack(fill="both", expand=True, padx=10, pady=8)
        self.entry.bind("<FocusIn>", lambda e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus(False))
        self._focused = False
        register(self)
        self.retheme()

    def _focus(self, v):
        self._focused = v
        self.retheme()

    def retheme(self):
        col = T["accent"] if self._focused else T["border"]
        self.configure(bg=T["surface2"], highlightbackground=col, highlightcolor=col)
        self.entry.configure(bg=T["surface2"], fg=T["fg"], insertbackground=T["fg"],
                             selectbackground=T["accent"], selectforeground=T["accent_fg"])


# ======================================================================= ظل ناعم
def shadow_image(w, h, blur=12, alpha=90, color=None):
    from PIL import ImageFilter
    color = color or T["paper_shadow"]
    s = 0.25 if w * h > 250000 else 1.0
    sw, sh, sb = max(2, int(w * s)), max(2, int(h * s)), max(1, int(blur * s))
    pad = sb * 3
    mask = Image.new("L", (sw + pad * 2, sh + pad * 2), 0)
    ImageDraw.Draw(mask).rectangle([pad, pad, pad + sw, pad + sh], fill=alpha)
    mask = mask.filter(ImageFilter.GaussianBlur(sb))
    im = Image.new("RGBA", mask.size, _hex(color) + (0,))
    im.putalpha(mask)
    if s != 1.0:
        im = im.resize((w + int(pad * 2 / s), h + int(pad * 2 / s)), Image.BILINEAR)
    return ImageTk.PhotoImage(im), (int(pad / s) if s != 1.0 else pad)
