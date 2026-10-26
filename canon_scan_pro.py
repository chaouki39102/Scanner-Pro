# -*- coding: utf-8 -*-
"""Canon Scan Pro — enhanced desktop scanning and document processing."""
import sys, os, tempfile, threading, traceback, io, json, shutil, math, time, queue, uuid
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageEnhance, ImageFilter, ImageOps
import numpy as np
import cv2
import scan_enhance as se
import ui_theme as ui
import re
import app_icon
import extras
import invoice_extract as ie
import invoice_ui
from ui_theme import T
from tkinter import font as tkfont

APP_NAME = "Canon Scan Pro"
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "Canon Scans")
os.makedirs(DEFAULT_DIR, exist_ok=True)

# ------------------------------------------------------------------ التصدير
# صيغ الإخراج المدعومة + الامتداد المقابل + هل هي بلا فقد؟
EXPORT_FORMATS = {
    "PNG":  (".png",  True),
    "JPEG": (".jpg",  False),
    "WEBP": (".webp", False),
    "TIFF": (".tif",  True),
    "BMP":  (".bmp",  True),
}
FORMAT_HINT = {
    "PNG":  "بلا فقد — مناسبة للوثائق الرسمية والأرشفة",
    "JPEG": "ملف أصغر — مناسب للمشاركة والطباعة",
    "WEBP": "أصغر من JPEG مع جودة قريبة — للويب",
    "TIFF": "بلا فقد متعدد الصفحات — للأرشفة الاحترافية",
    "BMP":  "بلا ضغط — كبير الحجم",
}

# مقاسات صفحات PDF بالنقاط (1 نقطة = 1/72 بوصة)
PDF_PAGE_SIZES = {
    "A4 (210×297 مم)": (595, 842),
    "A3 (297×420 مم)": (842, 1191),
    "Letter (216×279 مم)": (612, 792),
    "Legal (216×356 مم)": (612, 1008),
    "مقاس الصورة": None,
}

OCR_LANG_CHOICES = {
    "تلقائي: عربي + فرنسي + إنجليزي": None,
    "العربية": "ara",
    "الفرنسية": "fra",
    "الإنجليزية": "eng",
    "العربية + الإنجليزية": "ara+eng",
    "الفرنسية + الإنجليزية": "fra+eng",
}

OCR_ENGINE_CHOICES = {
    "تلقائي (الأفضل المتاح)": "auto",
    "Tesseract (عربي وفرنسي)": "tesseract",
    "RapidOCR (لاتيني وأرقام)": "rapidocr",
}

# حدود سجل التراجع: عدد خطوات + سقف ذاكرة (بايت) لتفادي استهلاكGBs
HISTORY_MAX_STEPS = 20
HISTORY_MAX_BYTES = 320 * 1024 * 1024

def _find_prop(props, prop_id):
    try:
        for p in props:
            if p.PropertyID == prop_id:
                return p
    except Exception:
        pass
    return None


def _set_prop(props, prop_id, value):
    p = _find_prop(props, prop_id)
    if p is None:
        return False
    try:
        p.Value = value
        return True
    except Exception:
        return False


def _get_value(props, prop_id, default=None):
    p = _find_prop(props, prop_id)
    if p is None:
        return default
    try:
        return p.Value
    except Exception:
        return default


def wia_scan(dpi=300, color=True):
    """Acquire one FULL page through Windows WIA COM (A4 flatbed area)."""
    try:
        import win32com.client
    except ImportError:
        raise RuntimeError("مكتبة pywin32 غير مثبتة. شغّل install.bat ثم أعد فتح البرنامج.")
    try:
        dm = win32com.client.Dispatch("WIA.DeviceManager")
        devices = []
        for info in dm.DeviceInfos:
            try:
                name = str(info.Properties("Name").Value)
            except Exception:
                name = "Scanner"
            devices.append((info, name))
        if not devices:
            raise RuntimeError("لم يعثر Windows على ماسح ضوئي عبر WIA. ثبّت تعريف Canon MF3010 وأعد تشغيل الجهاز.")
        chosen = None
        for info, name in devices:
            if "MF3010" in name.upper() or "CANON" in name.upper():
                chosen = info
                break
        if chosen is None:
            chosen = devices[0][0]
        device = chosen.Connect()
        item = device.Items(1)
        ip = item.Properties
        dp = device.Properties
        dpi = int(dpi)

        # 1) Intent first (changing it can reset other values), then data type.
        _set_prop(ip, 6146, 1 if color else 2)      # 1 = color, 2 = grayscale
        _set_prop(ip, 4103, 3 if color else 2)      # 3 = color, 2 = grayscale

        # 2) Resolution.
        _set_prop(ip, 6147, dpi)
        _set_prop(ip, 6148, dpi)

        # 3) Scan area: start at (0,0) and cover the whole bed.
        # Bed size is reported in 1/1000 inch; fall back to A4 if unavailable.
        bed_w = _get_value(dp, 3074, None)           # Horizontal Bed Size
        bed_h = _get_value(dp, 3075, None)           # Vertical Bed Size
        try:
            bed_w = int(bed_w) if bed_w else 8267
            bed_h = int(bed_h) if bed_h else 11692
        except Exception:
            bed_w, bed_h = 8267, 11692
        want_w = int(round(bed_w / 1000.0 * dpi))
        want_h = int(round(bed_h / 1000.0 * dpi))

        _set_prop(ip, 6149, 0)                       # Horizontal start
        _set_prop(ip, 6150, 0)                       # Vertical start

        # Clamp to what the driver allows at this DPI.
        for pid, want in ((6151, want_w), (6152, want_h)):
            p = _find_prop(ip, pid)
            value = want
            if p is not None:
                try:
                    mx = int(p.SubTypeMax)
                    if mx > 0:
                        value = min(want, mx)
                except Exception:
                    pass
            _set_prop(ip, pid, value)

        # Transfer as JPEG; fall back to the driver default format.
        try:
            image_file = item.Transfer("{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}")
        except Exception:
            image_file = item.Transfer()
        path = os.path.join(tempfile.gettempdir(), "canon_scan_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jpg")
        image_file.SaveFile(path)
        return _normalize_scan_file(path)
    except Exception as e:
        raise RuntimeError("تعذر تنفيذ المسح عبر WIA.\n"
            "تأكد من تشغيل الطابعة وتثبيت تعريف Canon MF3010، ثم جرّب تطبيق Windows Scan.\n"
            "تفاصيل النظام: " + str(e))


def _normalize_scan_file(path):
    """بعض تعريفات الماسح تعيد الملف بصيغة BMP كاملة الحجم (25 ميجابايت) رغم طلب JPEG.

    نحوّله هنا إلى JPEG حقيقي بنفس الاسم لتوفير المساحة وتسريع الفتح والمعالجة.
    """
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 2*1024*1024:
            return path
        with Image.open(path) as source:
            if (source.format or "").upper() not in ("BMP","TIFF"):
                return path
            image=source if source.mode in ("L","RGB") else source.convert("RGB")
            tmp=path+".conv.jpg"
            options={"format":"JPEG","quality":97}
            if image.mode=="RGB":options["subsampling"]=0
            dpi=source.info.get("dpi")
            if dpi:options["dpi"]=tuple(float(v) for v in dpi)
            image.save(tmp,**options)
        source.close()
        _replace_file(tmp,path)
        return path
    except Exception:
        try:
            if os.path.isfile(path+".conv.jpg"):os.remove(path+".conv.jpg")
        except Exception:pass
        return path


def _replace_file(tmp, path, attempts=6):
    """استبدال ملف على ويندوز مع إعادة المحاولة (مكافح الفيروسات قد يقفل الملف لحظيًا)."""
    for attempt in range(attempts):
        try:
            os.replace(tmp,path)
            return True
        except PermissionError:
            time.sleep(0.15*(attempt+1))
        except Exception:
            break
    try:
        shutil.copyfile(tmp,path)
        os.remove(tmp)
        return True
    except Exception:
        return False


FILTERS = {
    "ألوان طبيعية محسّنة": "magic",
    "أبيض وأسود واضح": "bw",
    "رمادي نظيف": "gray",
    "الصورة الأصلية": "original",
}
FILTER_PRESETS = {
    "مستند رسمي": {"mode":"bw", "crop":True, "deskew":True, "shadows":True, "denoise":True, "contrast":True, "sharp":True, "color":False},
    "فاتورة / جدول": {"mode":"bw", "crop":False, "deskew":True, "shadows":True, "denoise":False, "contrast":True, "sharp":True, "color":False},
    "صورة ملونة": {"mode":"magic", "crop":True, "deskew":True, "shadows":True, "denoise":True, "contrast":True, "sharp":True, "color":True},
    "إيصال باهت": {"mode":"gray", "crop":False, "deskew":True, "shadows":True, "denoise":True, "contrast":True, "sharp":True, "color":False},
    "بدون تحسين": {"mode":"original", "crop":False, "deskew":False, "shadows":False, "denoise":False, "contrast":False, "sharp":False, "color":False},
}


class Logo(tk.Canvas):
    def __init__(self, master):
        super().__init__(master, width=46, height=46, highlightthickness=0, bd=0)
        ui.register(self); self.retheme()

    def retheme(self):
        self.delete("all"); self.configure(bg=T["surface"])
        self._a = ImageTk.PhotoImage(app_icon.make_icon(46))
        self.create_image(0, 0, image=self._a, anchor="nw")


class EmptyState(tk.Canvas):
    """بطاقة الترحيب عند عدم وجود صفحات."""
    W, H = 500, 320

    def __init__(self, master, on_scan, on_open):
        super().__init__(master, width=self.W, height=self.H, highlightthickness=0, bd=0)
        self.b1 = ui.Btn(self, "بدء المسح الضوئي", on_scan, icon="scan", variant="primary", height=44, bg_token="surface")
        self.b2 = ui.Btn(self, "فتح صورة", on_open, icon="image", height=44, bg_token="surface")
        ui.register(self); self.retheme()

    def retheme(self):
        self.delete("all"); self.configure(bg=T["canvas"])
        W, H = self.W, self.H
        self._bg = ui.rr_image(W, H, 20, T["surface"], T["border"])
        self._ring = ui.rr_image(88, 88, 44, T["accent_soft"])
        self._ic = ui.icon_image("scan", 44, T["accent"])
        self.create_image(0, 0, image=self._bg, anchor="nw")
        self.create_image(W // 2 - 44, 34, image=self._ring, anchor="nw")
        self.create_image(W // 2, 78, image=self._ic)
        self.create_text(W // 2, 148, text="ابدأ بمسح مستند", fill=T["fg"], font=ui.F("h2"))
        self.create_text(W // 2, 178, anchor="n", width=420, justify="center", fill=T["fg2"], font=ui.F("body"),
                         text="ضع الورقة على زجاج الماسح ثم اضغط «بدء المسح الضوئي»،\nأو افتح صورة جاهزة لتحسينها وتصديرها.")
        self.create_window(W - 34, 262, window=self.b1, width=220, height=44, anchor="e")
        self.create_window(W - 34 - 220 - 12, 262, window=self.b2, width=200, height=44, anchor="e")

class App(tk.Tk):
    # >>> __init__
    def __init__(self):
        super().__init__()
        app_icon.apply_window_icon(self)
        self.title("Canon Scan Pro — مركز المسح وتحسين المستندات")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1440, sw - 60), min(900, sh - 90)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(1120, 680)
        self.pages=[]
        self.current=0
        self.working=False
        self.show_original=False
        self.output_dir=tk.StringVar(value=DEFAULT_DIR)
        self.dpi=tk.IntVar(value=300)
        self.filter_name=tk.StringVar(value="ألوان طبيعية محسّنة")
        self.preset=tk.StringVar(value="صورة ملونة")
        self.scan_color=tk.BooleanVar(value=False)
        self.mode_hint=tk.StringVar(value="")
        self._restoring=False
        self.opt_crop=tk.BooleanVar(value=True)
        self.opt_deskew=tk.BooleanVar(value=True)
        self.opt_shadows=tk.BooleanVar(value=True)
        self.opt_denoise=tk.BooleanVar(value=False)
        self.opt_clean_color=tk.BooleanVar(value=True)
        self.opt_ai=tk.BooleanVar(value=False)
        self.opt_contrast=tk.BooleanVar(value=True)
        self.opt_sharp=tk.BooleanVar(value=True)
        self.opt_color=tk.BooleanVar(value=False)
        self.opt_trim=tk.BooleanVar(value=True)
        self.opt_dust=tk.BooleanVar(value=True)
        self.opt_defringe=tk.BooleanVar(value=True)
        self.ink=tk.DoubleVar(value=0.0)
        self.white=tk.DoubleVar(value=50.0)
        self.brightness=tk.DoubleVar(value=0.0)
        self.saturation=tk.DoubleVar(value=0.0)
        self.gamma=tk.DoubleVar(value=1.0)
        # الإخراج
        self.out_format=tk.StringVar(value="PNG")
        self.out_quality=tk.IntVar(value=92)
        self.pdf_page=tk.StringVar(value="A4 (210×297 مم)")
        self.pdf_quality=tk.IntVar(value=90)
        self.pdf_title=tk.StringVar(value="Canon Scan Pro")
        self.blank_sens=tk.DoubleVar(value=0.5)
        self.opt_skip_blank=tk.BooleanVar(value=False)
        self.name_prefix=tk.StringVar(value="Scan")
        self.name_date=tk.BooleanVar(value=True)
        self.name_ocr=tk.BooleanVar(value=False)
        self.ocr_lang=tk.StringVar(value="تلقائي: عربي + فرنسي + إنجليزي")
        self.ocr_engine=tk.StringVar(value="تلقائي (الأفضل المتاح)")
        self.inv_each_page=tk.BooleanVar(value=True)
        self.fmt_hint=tk.StringVar(value=FORMAT_HINT["PNG"])
        self.est_text=tk.StringVar(value="")
        self.angle_entry=tk.StringVar(value="0")
        self.status=tk.StringVar(value="جاهز — ضع الورقة على زجاج Canon MF3010 ثم ابدأ المسح.")
        self._photo=None
        self._refs=[]
        self._resize_job=None
        self._canvas_img_rect=None
        self._crop_start=None
        self._crop_rect_id=None
        self._pan=[0,0]
        self._pan_ref=None
        self._pan_page=None
        self._chips=[]
        self.compare_mode=tk.BooleanVar(value=False)
        self.zoom=tk.DoubleVar(value=100.0)
        self._thumb_photos=[]
        self._drag_thumb=None
        self._history=[]
        self._redo=[]
        self._history_bytes=0
        self._est_cache={}
        self._est_job=None
        self._est_running=False
        self._est_pending=False
        self._ui_queue=queue.Queue()
        self._ui_job=None
        self._session_dir=os.environ.get("CANON_SCAN_SESSION_DIR") or os.path.join(DEFAULT_DIR, ".canon_scan_pro_session")
        os.makedirs(self._session_dir, exist_ok=True)
        prefs=self._load_prefs()
        ui.set_theme(prefs.get("theme","light"))
        self.dark_mode=tk.BooleanVar(value=ui.THEME_NAME=="dark")
        ui.init_fonts(self)
        self.configure(bg=T["bg"])
        self._style_scrollbar()
        self._build_ui()
        self._drain_ui_queue()
        self._restore_session()
        # Keyboard shortcuts. RTL: left arrow = next page, right arrow = previous page.
        self.bind("<Control-o>", lambda _e: self.open_image())
        self.bind("<Control-s>", lambda _e: self.save_image())
        self.bind("<Control-Shift-S>", lambda _e: self.save_pdf())
        self.bind("<Control-n>", lambda _e: self.new_session())
        self.bind("<Control-z>", lambda _e: self.undo())
        self.bind("<Control-i>", lambda _e: self.extract_invoices())
        self.bind("<Control-y>", lambda _e: self.redo())
        self.bind("<Control-0>", lambda _e: self.fit_page())
        self.bind("<Control-plus>", lambda _e: self.zoom_step(10))
        self.bind("<Control-equal>", lambda _e: self.zoom_step(10))
        self.bind("<Control-minus>", lambda _e: self.zoom_step(-10))
        self.bind("<Left>", lambda _e: None if self._typing() else self.move(1))
        self.bind("<Right>", lambda _e: None if self._typing() else self.move(-1))
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # تحديث تقدير الحجم عند تغيير الصيغة أو الجودة
        self.out_format.trace_add("write", lambda *a: self._schedule_estimate())
        self.out_quality.trace_add("write", lambda *a: self._schedule_estimate())
        self.pdf_page.trace_add("write", lambda *a: self._schedule_estimate())
        self.filter_name.trace_add("write", lambda *a: self._update_mode_hint())
        self.scan_color.trace_add("write", lambda *a: self._update_mode_hint())
        self._update_mode_hint()

    # >>> _mode_hint
    def _update_mode_hint(self):
        """يُظهر وضع الإخراج الحالي حتى لا يفاجئ المستخدم بنتيجة أبيض وأسود."""
        mode=FILTERS.get(self.filter_name.get(),"magic")
        names={"magic":"ألوان محسّنة (ملوّن)","bw":"أبيض وأسود","gray":"رمادي (تدرّج)","original":"أصلي (بلا معالجة)"}
        hint="وضع الإخراج الحالي: "+names.get(mode,mode)
        if mode in ("bw","gray") and not self.scan_color.get():
            hint+=" — سيُطلب من الماسح رماديًا (خطوط سوداء ناصعة بلا حواف ملوّنة)"
        elif self.scan_color.get():
            hint+=" — المسح من الماسح يكون ملوّنًا دائمًا"
        try:self.mode_hint.set(hint)
        except Exception:pass

    # >>> _typing
    def _typing(self):
        return isinstance(self.focus_get(), (tk.Entry, ttk.Entry))

    # >>> _prefs
    def _load_prefs(self):
        try:
            with open(os.path.join(self._session_dir,"prefs.json"),encoding="utf-8") as f:return json.load(f)
        except Exception:return {}

    def _save_prefs(self):
        try:
            data=self._load_prefs(); data["theme"]=ui.THEME_NAME
            with open(os.path.join(self._session_dir,"prefs.json"),"w",encoding="utf-8") as f:
                json.dump(data,f,ensure_ascii=False)
        except Exception:pass

    def _save_erp_cfg(self,url,token):
        try:
            data=self._load_prefs(); data["erp_url"]=url; data["erp_token"]=token
            with open(os.path.join(self._session_dir,"prefs.json"),"w",encoding="utf-8") as f:
                json.dump(data,f,ensure_ascii=False)
        except Exception:pass

    # >>> _style_scrollbar
    def _style_scrollbar(self):
        st=ttk.Style(self)
        try: st.theme_use("clam")
        except Exception: pass
        st.layout("Slim.Vertical.TScrollbar",[("Vertical.Scrollbar.trough",{"sticky":"ns","children":[("Vertical.Scrollbar.thumb",{"expand":"1","sticky":"nswe"})]})])
        st.configure("Slim.Vertical.TScrollbar",background=T["border_strong"],troughcolor=T["bg"],bordercolor=T["bg"],
                     lightcolor=T["border_strong"],darkcolor=T["border_strong"],gripcount=0,width=9,relief="flat")
        st.map("Slim.Vertical.TScrollbar",background=[("active",T["muted"]),("pressed",T["muted"])])

    # >>> _build_ui
    def _build_ui(self):
        self._build_header()
        self._build_statusbar()
        body=ui.Themed(self,"bg"); body.pack(fill="both",expand=True)
        self._build_sidebar(body)
        self._build_workspace(body)
        self._select_tab("scan")

    def _build_header(self):
        hdr=ui.Themed(self,"surface"); hdr.pack(fill="x")
        inner=ui.Themed(hdr,"surface"); inner.pack(fill="x",padx=22,pady=11)
        Logo(inner).pack(side="right")
        tf=ui.Themed(inner,"surface"); tf.pack(side="right",padx=14)
        ui.label(tf,"Canon Scan Pro","h1","fg","surface",anchor="e").pack(fill="x")
        ui.label(tf,"مركز المسح وتحسين المستندات","small","muted","surface",anchor="e").pack(fill="x")
        self.btn_theme=ui.IconBtn(inner,"sun" if ui.THEME_NAME=="dark" else "moon",self.toggle_theme,tip="تبديل المظهر: فاتح / داكن",bg_token="surface")
        self.btn_theme.pack(side="left")
        ui.Themed(inner,"surface",width=10).pack(side="left")
        self.btn_redo=ui.IconBtn(inner,"redo",self.redo,tip="إعادة (Ctrl+Y)",bg_token="surface"); self.btn_redo.pack(side="left",padx=2)
        self.btn_undo=ui.IconBtn(inner,"undo",self.undo,tip="تراجع (Ctrl+Z)",bg_token="surface"); self.btn_undo.pack(side="left",padx=2)
        ui.Btn(inner,"جلسة جديدة",self.new_session,icon="clear",height=36,pad=10,
               tip="بدء جلسة جديدة وإزالة كل الصفحات (Ctrl+N)").pack(side="left",padx=(8,0))
        ui.HSep(hdr).pack(fill="x")

    def _build_statusbar(self):
        sb=ui.Themed(self,"surface"); sb.pack(fill="x",side="bottom")
        ui.HSep(sb).pack(fill="x")
        row=ui.Themed(sb,"surface"); row.pack(fill="x",padx=20,pady=6)
        ui.label(row,"Ctrl+N جلسة جديدة   •   Ctrl+O فتح   •   Ctrl+S حفظ   •   Ctrl+Z تراجع   •   عجلة الفأرة للتكبير   •   زر الفأرة الأيمن للتحريك","small","muted","surface",anchor="w").pack(side="left")
        ui.label(row,"","small","fg2","surface",anchor="e",textvariable=self.status).pack(side="right",fill="x")

    # ------------------------------------------------------------ اللوحة اليمنى
    def _build_sidebar(self,body):
        side=ui.Themed(body,"surface",width=412); side.pack(side="right",fill="y"); side.pack_propagate(False)
        ui.Themed(body,"border",width=1).pack(side="right",fill="y")
        self.tabs=ui.Tabs(side,[("scan","المسح","scan"),("enhance","التحسين","wand"),("edit","التحرير","crop"),("export","التصدير","export"),("tools","أدوات","layers")],self._select_tab)
        self.tabs.pack(fill="x")
        # الشريط السفلي الثابت: أهم الأزرار تظهر دائمًا
        foot=ui.Themed(side,"surface"); foot.pack(side="bottom",fill="x")
        ui.HSep(foot).pack(fill="x")
        pad=ui.Themed(foot,"surface"); pad.pack(fill="x",padx=16,pady=(14,12))
        self.btn_scan=ui.Btn(pad,"بدء المسح الضوئي",self.scan,icon="scan",variant="primary",height=48)
        self.btn_scan.pack(fill="x")
        row=ui.Themed(pad,"surface"); row.pack(fill="x",pady=(8,0))
        ui.Btn(row,"تصدير كصورة",self.export_image,icon="export",height=38,pad=8,font="small_b",tip="تصدير الصفحة الحالية كصورة بالصيغة المختارة في تبويب التصدير (Ctrl+S)").pack(side="right",expand=True,fill="x",padx=(0,3))
        ui.Btn(row,"فتح صورة",self.open_image,icon="image",height=38,pad=8,font="small_b",tip="فتح صورة كصفحة جديدة (Ctrl+O)").pack(side="right",expand=True,fill="x",padx=3)
        ui.Btn(row,"تصدير PDF",self.save_pdf,icon="pdf",height=38,pad=8,font="small_b",tip="تصدير كل الصفحات إلى PDF (Ctrl+Shift+S)").pack(side="right",expand=True,fill="x",padx=(3,0))
        self.progress=ui.Progress(pad); self.progress.pack(fill="x",pady=(12,0))
        # منطقة المحتوى القابلة للتمرير
        area=ui.Themed(side,"bg"); area.pack(fill="both",expand=True)
        self.side_area=area
        vsb=ttk.Scrollbar(area,orient="vertical",style="Slim.Vertical.TScrollbar")
        self._vsb=vsb
        def _sb_set(first,last):
            vsb.set(first,last)
            if float(first)<=0.0 and float(last)>=1.0:vsb.pack_forget()
            elif not vsb.winfo_ismapped():vsb.pack(side="left",fill="y",before=self.side_canvas)
        self.side_canvas=tk.Canvas(area,highlightthickness=0,bd=0,bg=T["bg"],yscrollcommand=_sb_set)
        vsb.configure(command=self.side_canvas.yview)
        self.side_canvas.pack(side="right",fill="both",expand=True)
        host=ui.Themed(self.side_canvas,"bg")
        win=self.side_canvas.create_window((0,0),window=host,anchor="nw")
        host.bind("<Configure>",lambda _e:self.side_canvas.configure(scrollregion=self.side_canvas.bbox("all")))
        self.side_canvas.bind("<Configure>",lambda e:self.side_canvas.itemconfigure(win,width=e.width))
        self.tab_frames={}
        for key,builder in (("scan",self._tab_scan),("enhance",self._tab_enhance),("edit",self._tab_edit),("export",self._tab_export),("tools",self._tab_tools)):
            fr=ui.Themed(host,"bg"); builder(fr); self.tab_frames[key]=fr

    def _select_tab(self,key):
        for k,fr in self.tab_frames.items():fr.pack_forget()
        self.tab_frames[key].pack(fill="x",pady=(0,16))
        self.tabs.active=key; self.tabs._draw()
        self.side_canvas.yview_moveto(0)

    def _card(self,parent,title,icon,first=False):
        c=ui.Card(parent,title,icon); c.pack(fill="x",padx=16,pady=(16 if first else 12,0)); return c.body

    def _row(self,parent,pady=(8,0)):
        r=ui.Themed(parent,"surface"); r.pack(fill="x",pady=pady); return r

    def _tab_scan(self,p):
        b=self._card(p,"المسح الضوئي","scan",True)
        ui.label(b,"دقة المسح (DPI)","small","fg2").pack(fill="x")
        ui.Segmented(b,[(v,str(v)) for v in (150,200,300,400,600)],self.dpi).pack(fill="x",pady=(6,14))
        ui.label(b,"نوع التحسين الأساسي","small","fg2").pack(fill="x")
        ui.Choice(b,[(k,k) for k in FILTERS],self.filter_name).pack(fill="x",pady=(6,0))
        ui.label(b,"","small_b","accent",textvariable=self.mode_hint,wraplength=320).pack(fill="x",pady=(8,0))
        ui.Switch(b,"مسح بالألوان من الماسح دائمًا",self.scan_color,
                  sub="مطفأ: المسح رمادي في وضعي أبيض وأسود/رمادي (خطوط سوداء ناصعة بلا حواف حمراء/خضراء). شغّله لتبقى المعاينة ملوّنة وتغيّر الفلتر لاحقًا دون إعادة المسح").pack(fill="x",pady=(4,0))
        b=self._card(p,"إعدادات جاهزة","wand")
        ui.label(b,"اختر نوع المستند ليُضبط الفلتر والخيارات دفعة واحدة.","small","muted",wraplength=320).pack(fill="x",pady=(0,8))
        grid=ui.Themed(b,"surface"); grid.pack(fill="x")
        grid.columnconfigure((0,1),weight=1,uniform="chips")
        for i,name in enumerate(FILTER_PRESETS):
            ch=ui.Chip(grid,name,lambda n=name:self._choose_preset(n),lambda n=name:self.preset.get()==n)
            last_odd=(i==len(FILTER_PRESETS)-1 and len(FILTER_PRESETS)%2==1)
            if last_odd: ch.grid(row=i//2,column=0,columnspan=2,sticky="ew",padx=3,pady=3)
            else: ch.grid(row=i//2,column=1-(i%2),sticky="ew",padx=3,pady=3)
            self._chips.append(ch)
        self.preset.trace_add("write",lambda *a:[c._draw() for c in self._chips])

    def _choose_preset(self,name):
        self.preset.set(name); self.apply_preset()

    def _tab_enhance(self,p):
        w=ui.Themed(p,"bg"); w.pack(fill="x",padx=16,pady=(16,0))
        row=ui.Themed(w,"bg"); row.pack(fill="x")
        ui.Btn(row,"تطبيق على الكل",self.reprocess_all,icon="layers",height=44,bg_token="bg").pack(side="right",expand=True,fill="x",padx=(0,6))
        ui.Btn(row,"تطبيق على الصفحة",self.reprocess,icon="wand",variant="primary",height=44,bg_token="bg").pack(side="right",expand=True,fill="x",padx=(6,0))
        b=self._card(p,"التحسينات","wand")
        for text,var,sub in (
            ("قص حدود الورقة تلقائيًا",self.opt_crop,None),
            ("إزالة حواف الماسح السوداء",self.opt_trim,"مطلوب لتصحيح الميلان"),
            ("تسوية ميلان النص تلقائيًا",self.opt_deskew,None),
            ("إزالة الظلال وتوحيد الإضاءة",self.opt_shadows,None),
            ("تقليل التشويش",self.opt_denoise,None),
            ("تنقية تحافظ على الأختام والألوان",self.opt_clean_color,None),
            ("إزالة حواف الألوان الرفيعة عن الخطوط",self.opt_defringe,"شوائب حمراء/خضراء من مستشعرات الماسح"),
            ("إزالة الغبار النقطي (أبيض وأسود)",self.opt_dust,None),
            ("زيادة التباين",self.opt_contrast,None),
            ("زيادة حدة الكتابة",self.opt_sharp,None),
            ("تعميق ألوان الصور",self.opt_color,None),
            ("رفع الدقة بالذكاء الاصطناعي 2×",self.opt_ai,"قد يحتاج تنزيل النموذج أول مرة")):
            ui.Switch(b,text,var,sub=sub).pack(fill="x")
        b=self._card(p,"ضبط يدوي","sliders")
        ui.Slider(b,"قوة الحبر / سماكة الخط",self.ink,-2,2,"{:+.1f}",default=0.0).pack(fill="x")
        ui.Slider(b,"السطوع",self.brightness,-0.5,0.5,"{:+.2f}",default=0.0).pack(fill="x",pady=(4,0))
        ui.Slider(b,"التشبّع",self.saturation,-0.5,0.5,"{:+.2f}",default=0.0).pack(fill="x",pady=(4,0))
        ui.Slider(b,"الجيما (Gamma)",self.gamma,0.5,2.0,"{:.2f}",default=1.0).pack(fill="x",pady=(4,0))
        ui.Slider(b,"بياض الخلفية",self.white,0,100,"{:.0f}%",default=50.0).pack(fill="x",pady=(4,0))
        ui.label(b,"نقرة مزدوجة على أي منزلق تعيده إلى القيمة الافتراضية.","small","muted",wraplength=320).pack(fill="x",pady=(6,0))

    def _tab_edit(self,p):
        b=self._card(p,"التدوير","rotate",True)
        ui.label(b,"زاوية دقيقة (بالدرجات)","small","fg2").pack(fill="x")
        ui.label(b,"الموجب = عكس عقارب الساعة (مثل زاوية الميلان التلقائية المعروضة في شريط الحالة).","small","muted",wraplength=320).pack(fill="x",pady=(2,0))
        r=self._row(b,(6,0))
        ui.Btn(r,"−1°",lambda:self.rotate_degrees(-1),height=38).pack(side="left",expand=True,fill="x",padx=(0,6))
        ui.Field(r,self.angle_entry,width=6,justify="center").pack(side="left",padx=0)
        ui.Btn(r,"+1°",lambda:self.rotate_degrees(1),height=38).pack(side="left",expand=True,fill="x",padx=(6,0))
        ui.Btn(b,"تطبيق الزاوية",self.apply_angle,variant="soft",height=38).pack(fill="x",pady=(8,0))
        r=self._row(b,(8,0))
        ui.Btn(r,"90° يمينًا",lambda:self.rotate_degrees(-90),icon="rot_right",height=38).pack(side="right",expand=True,fill="x",padx=(4,0))
        ui.Btn(r,"90° يسارًا",lambda:self.rotate_degrees(90),icon="rot_left",height=38).pack(side="right",expand=True,fill="x",padx=(0,4))
        b=self._card(p,"القص","crop")
        ui.Btn(b,"بدء تحديد القص",self.start_crop,icon="crop",height=38).pack(fill="x")
        r=self._row(b)
        ui.Btn(r,"قص التحديد",self.apply_crop,variant="primary",height=38).pack(side="right",expand=True,fill="x",padx=(4,0))
        ui.Btn(r,"إلغاء القص",self.reset_crop,height=38).pack(side="right",expand=True,fill="x",padx=(0,4))
        b=self._card(p,"العرض","eye")
        r=self._row(b,(0,0))
        self.btn_orig=ui.Btn(r,"عرض الأصل",self.toggle_original,icon="eye",height=38)
        self.btn_orig.pack(side="right",expand=True,fill="x",padx=(4,0))
        self.btn_compare=ui.Btn(r,"مقارنة",self.toggle_compare,icon="compare",height=38)
        self.btn_compare.pack(side="right",expand=True,fill="x",padx=(0,4))

    def _tab_export(self,p):
        b=self._card(p,"مجلد الحفظ","folder",True)
        r=self._row(b,(0,0))
        ui.IconBtn(r,"folder",self.pick_folder,tip="اختيار مجلد",size=40,bg_token="surface",variant="secondary").pack(side="right",padx=(8,0))
        ui.Field(r,self.output_dir,width=10,justify="left").pack(side="right",fill="x",expand=True)
        b=self._card(p,"صيغة الإخراج","export")
        ui.label(b,"الصيغة التي ستُصدَّر بها الصور","small","fg2").pack(fill="x")
        ui.Choice(b,[(k,k) for k in EXPORT_FORMATS],self.out_format).pack(fill="x",pady=(6,0))
        ui.label(b,"معلومات الصيغة","small","muted",wraplength=320,textvariable=self.fmt_hint).pack(fill="x",pady=(6,0))
        ui.Slider(b,"جودة الضغط (JPEG / WEBP)",self.out_quality,50,100,"{:.0f}%",default=92).pack(fill="x",pady=(6,0))
        ui.label(b,"الحجم المتوقع بعد التصدير","small","fg2").pack(fill="x",pady=(10,0))
        est=ui.Themed(b,"surface2"); est.pack(fill="x",pady=(6,0))
        ui.label(est,"", "small_b","fg2","surface2",anchor="e",textvariable=self.est_text,wraplength=320).pack(fill="x",padx=10,pady=10)
        b=self._card(p,"الحفظ والتصدير","save")
        ui.Btn(b,"تصدير الصفحة الحالية كصورة",self.export_image,icon="export",height=40).pack(fill="x")
        ui.Btn(b,"تصدير الصفحة الحالية PDF",lambda:self.save_pdf(current_only=True),icon="pdf",height=40).pack(fill="x",pady=(8,0))
        ui.Btn(b,"تصدير كل الصفحات PDF",self.save_pdf,icon="pdf",variant="soft",height=40).pack(fill="x",pady=(8,0))
        ui.Btn(b,"تصدير كل الصفحات TIFF",self.save_multipage_tiff,icon="layers",height=40).pack(fill="x",pady=(8,0))
        ui.Btn(b,"طباعة / إرسال إلى الطابعة",self.print_pages,icon="export",height=40).pack(fill="x",pady=(8,0))
        ui.HSep(b).pack(fill="x",pady=12)
        ui.label(b,"خيارات ملف PDF","small","fg2").pack(fill="x",pady=(0,6))
        ui.Choice(b,[(k,k) for k in PDF_PAGE_SIZES],self.pdf_page).pack(fill="x",pady=(6,0))
        ui.Slider(b,"جودة صور PDF",self.pdf_quality,50,100,"{:.0f}%",default=90).pack(fill="x",pady=(6,0))
        r=self._row(b,(6,0))
        ui.label(r,"عنوان المستند في PDF","small","fg2").pack(side="right")
        ui.Field(r,self.pdf_title,width=12,justify="left").pack(side="right",fill="x",expand=True,padx=(8,0))
        ui.HSep(b).pack(fill="x",pady=12)
        ui.label(b,"التعرّف على النص (OCR عربي / فرنسي)","small","fg2").pack(fill="x",pady=(0,6))
        ui.label(b,"محرك التعرّف","small","fg2").pack(fill="x",pady=(6,0))
        ui.Choice(b,[(k,k) for k in OCR_ENGINE_CHOICES],self.ocr_engine).pack(fill="x",pady=(6,0))
        ui.label(b,"اختر لغة التعرّف","small","fg2").pack(fill="x",pady=(10,0))
        ui.Choice(b,[(k,k) for k in OCR_LANG_CHOICES],self.ocr_lang).pack(fill="x",pady=(6,0))
        ui.label(b,"Tesseract هو الأفضل للعربية عند تثبيت ملفات اللغة. RapidOCR محلي وسريع ويدعم اللاتينية والأرقام (ونموذجه الافتراضي لا يقرأ العربية).","small","muted",wraplength=320).pack(fill="x",pady=(6,0))
        ui.Btn(b,"استخراج النص إلى TXT",self.export_ocr_text,icon="text",height=40).pack(fill="x",pady=(6,0))
        ui.Btn(b,"إنشاء PDF قابل للبحث",self.save_searchable_pdf,icon="search_pdf",height=40).pack(fill="x",pady=(8,0))
        b=self._card(p,"استخراج بيانات الفاتورة","doc")
        ui.label(b,"يقرأ رقم الفاتورة والتاريخ والمورّد وNIF وNIS وRC وAI والمبالغ والبنود، ثم يتحقق حسابيًا (HT + TVA = TTC) ويعرض النتيجة للمراجعة قبل التصدير إلى Excel أو ERP.","small","muted",wraplength=320).pack(fill="x",pady=(0,8))
        ui.Switch(b,"كل صفحة فاتورة مستقلة",self.inv_each_page,sub="أوقفه إن كانت الصفحات كلها فاتورة واحدة").pack(fill="x")
        ui.Btn(b,"استخراج بيانات الفاتورة",self.extract_invoices,icon="doc",variant="primary",height=42).pack(fill="x",pady=(8,0))
        b=self._card(p,"إدارة الصفحات","layers")
        r=self._row(b,(0,0))
        ui.Btn(r,"حذف الصفحة",self.delete_page,icon="trash",variant="danger",height=38).pack(side="right",expand=True,fill="x",padx=(4,0))
        ui.Btn(r,"إفراغ الكل",self.clear_pages,icon="clear",variant="danger",height=38).pack(side="right",expand=True,fill="x",padx=(0,4))
        ui.Btn(b,"بدء جلسة جديدة",self.new_session,icon="clear",variant="soft",height=38).pack(fill="x",pady=(8,0))
        ui.label(b,"الجلسة الجديدة تحذف صفحات العمل الحالية من البرنامج فقط، ولا تحذف الملفات التي حفظتها على القرص.","small","muted",wraplength=320).pack(fill="x",pady=(6,0))

    def _tab_tools(self,p):
        b=self._card(p,"تنظيف المستند","clear",True)
        ui.Slider(b,"حساسية كشف الصفحة الفارغة",self.blank_sens,0,1,"{:.0%}",default=0.5).pack(fill="x")
        ui.Btn(b,"حذف الصفحات الفارغة",self.remove_blank_pages,icon="trash",height=40).pack(fill="x",pady=(8,0))
        ui.Switch(b,"تخطي الصفحة الفارغة تلقائيًا بعد المسح",self.opt_skip_blank,sub="مفيد عند المسح المزدوج").pack(fill="x",pady=(8,0))
        b=self._card(p,"التسمية الذكية للملفات","text")
        r=self._row(b,(0,0))
        ui.label(r,"بادئة الاسم","small","fg2").pack(side="right")
        ui.Field(r,self.name_prefix,width=12,justify="left").pack(side="right",fill="x",expand=True,padx=(8,0))
        ui.Switch(b,"إضافة التاريخ والوقت",self.name_date).pack(fill="x",pady=(8,0))
        ui.Switch(b,"استخدام أول سطر من النص (OCR)",self.name_ocr,sub="يحتاج Tesseract").pack(fill="x")
        b=self._card(p,"استيراد","pdf")
        ui.Btn(b,"فتح PDF كصفحات للتحرير",self.import_pdf,icon="pdf",height=40).pack(fill="x")
        b=self._card(p,"أدوات PDF (ملفات خارجية)","layers")
        for a,bb in ((("دمج ملفات PDF",self.tool_merge,"layers"),("تقسيم PDF",self.tool_split,"crop")),
                     (("استخراج صفحات",self.tool_extract,"export"),("حذف صفحات",self.tool_delete,"trash")),
                     (("تدوير صفحات",self.tool_rotate,"wand"),("حماية بكلمة مرور",self.tool_protect,"save"))):
            r=self._row(b,(8,0))
            for (t,cmd,ic),padx in ((a,(0,3)),(bb,(3,0))):
                ui.Btn(r,t,cmd,icon=ic,height=38,pad=8,font="small_b").pack(side="right",expand=True,fill="x",padx=padx)
        ui.Btn(b,"ضغط PDF",self.tool_compress,icon="pdf",height=38,variant="soft").pack(fill="x",pady=(8,0))
        ui.label(b,"كل العمليات محلية بالكامل ولا تعدّل الملف الأصلي؛ يُحفظ ناتج جديد.","small","muted",wraplength=320).pack(fill="x",pady=(8,0))

    # ------------------------------------------------------------ منطقة العمل
    def _build_workspace(self,body):
        work=ui.Themed(body,"bg"); work.pack(side="right",fill="both",expand=True,padx=18,pady=(14,12))
        tb=ui.Themed(work,"bg"); tb.pack(fill="x",pady=(0,12))
        # يمين: التنقل بين الصفحات
        self.btn_prev=ui.IconBtn(tb,"prev",lambda:self.move(-1),tip="الصفحة السابقة (→)",bg_token="bg",variant="secondary"); self.btn_prev.pack(side="right")
        self.page_label=ui.label(tb,"لا توجد صفحات","h3","fg","bg",anchor="center",width=22)
        self.page_label.pack(side="right",padx=8)
        self.btn_next=ui.IconBtn(tb,"next",lambda:self.move(1),tip="الصفحة التالية (←)",bg_token="bg",variant="secondary"); self.btn_next.pack(side="right")
        # يسار: التكبير
        ui.IconBtn(tb,"fit",self.fit_page,tip="ملاءمة (Ctrl+0)",bg_token="bg",variant="secondary").pack(side="left")
        ui.IconBtn(tb,"minus",lambda:self.zoom_step(-10),tip="تصغير",bg_token="bg",variant="secondary").pack(side="left",padx=(8,0))
        self.zoom_label=ui.label(tb,"100%","body_b","fg2","bg",anchor="center",width=6); self.zoom_label.pack(side="left")
        ui.IconBtn(tb,"plus",lambda:self.zoom_step(10),tip="تكبير",bg_token="bg",variant="secondary").pack(side="left")
        # الوسط: أدوات العرض السريعة
        self.btn_compare2=ui.IconBtn(tb,"compare",self.toggle_compare,tip="مقارنة الأصل بالنتيجة",bg_token="bg",variant="secondary")
        self.btn_compare2.pack(side="right",padx=(18,0))
        self.btn_orig2=ui.IconBtn(tb,"eye",self.toggle_original,tip="عرض الصورة الأصلية",bg_token="bg",variant="secondary")
        self.btn_orig2.pack(side="right",padx=(8,0))
        # الصفحات المصغّرة (أسفل)
        strip=ui.Themed(work,"bg"); strip.pack(side="bottom",fill="x",pady=(12,0)); self.strip=strip
        hd=ui.Themed(strip,"bg"); hd.pack(fill="x",pady=(0,4))
        self.strip_title=ui.label(hd,"الصفحات","h3","fg","bg",anchor="e"); self.strip_title.pack(side="right")
        ui.label(hd,"اسحب المصغّر بزر الفأرة الأيمن لإعادة الترتيب","small","muted","bg",anchor="w").pack(side="left")
        self.thumb_canvas=tk.Canvas(strip,height=118,highlightthickness=0,bd=0,bg=T["bg"])
        self.thumb_canvas.pack(fill="x")
        self.thumb_inner=tk.Frame(self.thumb_canvas,bg=T["bg"],bd=0)
        self.thumb_window=self.thumb_canvas.create_window((0,0),window=self.thumb_inner,anchor="nw")
        self.thumb_inner.bind("<Configure>",self._layout_thumbs)
        self.thumb_canvas.bind("<Configure>",self._layout_thumbs)
        # لوحة المعاينة
        self.canvas=tk.Canvas(work,bg=T["canvas"],highlightthickness=1,highlightbackground=T["border"],highlightcolor=T["border"],bd=0,cursor="arrow")
        self.canvas.pack(fill="both",expand=True)
        self.empty=EmptyState(self.canvas,self.scan,self.open_image)
        self.canvas.bind("<Configure>",self._on_resize)
        self.canvas.bind("<ButtonPress-1>",self._crop_press)
        self.canvas.bind("<B1-Motion>",self._crop_drag)
        self.canvas.bind("<ButtonRelease-1>",self._crop_release)
        self.canvas.bind("<ButtonPress-3>",self._pan_start)
        self.canvas.bind("<B3-Motion>",self._pan_move)
        self.canvas.bind("<ButtonRelease-3>",lambda _e:setattr(self,"_pan_ref",None))
        self._layout_thumbs()

    def _post_ui(self,callback):
        """Queue a Tkinter callback for the main thread (safe from worker threads)."""
        self._ui_queue.put(callback)

    def _drain_ui_queue(self):
        self._ui_job=None
        try:
            while True:
                callback=self._ui_queue.get_nowait()
                try:callback()
                except Exception:traceback.print_exc()
        except queue.Empty:
            pass
        try:self._ui_job=self.after(50,self._drain_ui_queue)
        except Exception:pass

    def _on_close(self):
        """إلغاء مهام after المعلّقة قبل إغلاق النافذة الرئيسية."""
        for attr in ("_ui_job","_est_job","_resize_job"):
            job=getattr(self,attr,None)
            if job:
                try:self.after_cancel(job)
                except Exception:pass
                setattr(self,attr,None)
        self.destroy()

    # >>> _set_busy
    def _set_busy(self,flag,msg=None):
        self.working=flag
        if flag: self.progress.start()
        else: self.progress.stop()
        self.btn_scan.set_text("جارٍ المعالجة…" if flag else "بدء المسح الضوئي")
        self.btn_scan.set_state("disabled" if flag else "normal")
        if msg: self.status.set(msg)

    # >>> _on_wheel
    def _on_wheel(self,e):
        try:w=self.winfo_containing(e.x_root,e.y_root)
        except Exception:return
        while w is not None:
            if w is self.canvas:
                self.zoom_step(10 if e.delta>0 else -10); return
            if w is self.side_area:
                bbox=self.side_canvas.bbox("all")
                if bbox and bbox[3]-bbox[1]>self.side_canvas.winfo_height():
                    self.side_canvas.yview_scroll(int(-1*(e.delta/120)),"units")
                return
            if w is self.thumb_canvas:
                self.thumb_canvas.xview_scroll(int(-1*(e.delta/120))*2,"units"); return
            w=getattr(w,"master",None)

    def zoom_step(self,d):
        if not self.pages:return
        self.zoom.set(max(25.0,min(300.0,round(self.zoom.get()+d))))
        self.show_page()

    def _pan_start(self,e):
        self._pan_ref=(e.x,e.y)

    def _pan_move(self,e):
        if not self._pan_ref or not self.pages:return
        dx,dy=e.x-self._pan_ref[0],e.y-self._pan_ref[1]; self._pan_ref=(e.x,e.y)
        self.canvas.move("pg",dx,dy); self._pan[0]+=dx; self._pan[1]+=dy
        if self._canvas_img_rect:
            x1,y1,x2,y2=self._canvas_img_rect; self._canvas_img_rect=(x1+dx,y1+dy,x2+dx,y2+dy)
        if hasattr(self,"_crop_pixels"):
            a,b,c,d=self._crop_pixels; self._crop_pixels=(a+dx,b+dy,c+dx,d+dy)
        if self._crop_start:
            self._crop_start=(self._crop_start[0]+dx,self._crop_start[1]+dy)
        if self._crop_rect_id:
            self.canvas.move(self._crop_rect_id,dx,dy)

    # >>> show_page
    def _pill(self,x,y,text,fill,fg,anchor="center"):
        f=tkfont.Font(font=ui.F("small_b")); w=f.measure(text)+26; h=26
        im=ui.rr_image(w,h,13,fill); self._refs.append(im)
        px=x-w//2 if anchor=="center" else x-w
        self.canvas.create_image(px,y-h//2 if anchor=="center" else y,image=im,anchor="nw")
        self.canvas.create_text(px+w//2,(y if anchor=="center" else y+h//2),text=text,fill=fg,font=ui.F("small_b"))

    def _draw_page_image(self,im,x,y):
        w,h=im.size
        sh,pad=ui.shadow_image(w,h); self._refs.append(sh)
        self.canvas.create_image(x-pad,y-pad+7,image=sh,anchor="nw",tags="pg")
        ph=ImageTk.PhotoImage(im); self._refs.append(ph); self._photo=ph
        self.canvas.create_image(x,y,image=ph,anchor="nw",tags=("pg","page"))
        self.canvas.create_rectangle(x,y,x+w,y+h,outline=T["border_strong"],tags="pg")

    def show_page(self):
        self._resize_job=None
        c=self.canvas
        self._reset_crop_selection()
        c.delete("all"); self._canvas_img_rect=None; self._refs=[]
        cw=max(200,c.winfo_width()); ch=max(200,c.winfo_height())
        if not self.pages:
            self.strip.pack_forget()
            self.empty.place(in_=c,relx=.5,rely=.5,anchor="center")
            self.page_label.configure(text="لا توجد صفحات")
            self.zoom_label.configure(text=f"{int(self.zoom.get())}%")
            self._refresh_thumbnails(); self._update_nav()
            return
        self.empty.place_forget()
        if not self.strip.winfo_ismapped():self.strip.pack(side="bottom",fill="x",pady=(12,0),before=self.canvas)
        page=self.pages[self.current]
        if self._pan_page is not page:
            self._pan=[0,0]; self._pan_page=page
        z=self.zoom.get()/100.0
        if self.compare_mode.get():
            half=cw//2
            boxw=max(120,half-56); boxh=max(120,ch-110)
            def fitted(img):
                s=min(boxw/max(1,img.width),boxh/max(1,img.height),2.0)*z
                return img.resize((max(1,int(img.width*s)),max(1,int(img.height*s))),Image.Resampling.LANCZOS)
            enh=fitted(page["preview"]); orig=fitted(page["orig_preview"])
            ye=(ch-enh.height)//2+14+self._pan[1]; yo=(ch-orig.height)//2+14+self._pan[1]
            xe=(half-enh.width)//2+self._pan[0]; xo=half+(half-orig.width)//2+self._pan[0]
            self._draw_page_image(orig,xo,yo); self._draw_page_image(enh,xe,ye)
            self._pill(xo+orig.width//2,22,"الأصل",T["track"],T["fg2"])
            self._pill(xe+enh.width//2,22,"بعد التحسين",T["accent"],T["accent_fg"])
            self._canvas_img_rect=(xe,ye,xe+enh.width,ye+enh.height)
        else:
            im=(page["orig_preview"] if self.show_original else page["preview"])
            s=min(max(200,cw-70)/max(1,im.width),max(200,ch-70)/max(1,im.height),2.0)*z
            im=im.resize((max(1,int(im.width*s)),max(1,int(im.height*s))),Image.Resampling.LANCZOS if s<=1 else Image.Resampling.BICUBIC)
            x=(cw-im.width)//2+self._pan[0]; y=(ch-im.height)//2+self._pan[1]
            self._canvas_img_rect=(x,y,x+im.width,y+im.height)
            self._draw_page_image(im,x,y)
        label=f"الصفحة {self.current+1} / {len(self.pages)}"+(" — الأصل" if self.show_original else " — محسّنة")
        if page.get("processing"):
            label += " • جارٍ التحسين"
            self._pill(cw-16,16,"الصورة الأصلية — التحسين جارٍ…",T["warn"],T["warn_fg"],anchor="ne")
        elif self.show_original and not self.compare_mode.get():
            self._pill(cw-16,16,"عرض الأصل",T["track"],T["fg2"],anchor="ne")
        self.page_label.configure(text=label)
        self.zoom_label.configure(text=f"{int(self.zoom.get())}%")
        self._refresh_thumbnails(); self._update_nav()

    def _update_nav(self):
        n=len(self.pages)
        self.btn_prev.set_state("normal" if n and self.current>0 else "disabled")
        self.btn_next.set_state("normal" if n and self.current<n-1 else "disabled")
        self.btn_undo.set_state("normal" if self._history else "disabled")
        self.btn_redo.set_state("normal" if self._redo else "disabled")
        for b,on in ((self.btn_compare,self.compare_mode.get()),(self.btn_compare2,self.compare_mode.get()),
                     (self.btn_orig,self.show_original),(self.btn_orig2,self.show_original)):
            b.variant="soft" if on else "secondary"; b._draw()
        self.strip_title.configure(text=f"الصفحات ({n})" if n else "الصفحات")

    # >>> _refresh_thumbnails
    def _layout_thumbs(self,_e=None):
        tc,ti=self.thumb_canvas,self.thumb_inner
        iw=ti.winfo_reqwidth(); cw=max(1,tc.winfo_width())
        tc.coords(self.thumb_window,max(0,cw-iw),0)
        tc.configure(scrollregion=(0,0,max(cw,iw),118))

    def _refresh_thumbnails(self):
        if not hasattr(self,"thumb_inner"): return
        for child in self.thumb_inner.winfo_children(): child.destroy()
        self._thumb_photos=[]
        TW,TH=92,112
        for idx,page in enumerate(self.pages):
            sel=idx==self.current
            cv=tk.Canvas(self.thumb_inner,width=TW,height=TH,highlightthickness=0,bd=0,bg=T["bg"],cursor="hand2")
            card=ui.rr_image(TW-4,TH-4,12,T["accent_soft"] if sel else T["surface"],T["accent"] if sel else T["border"],2 if sel else 1)
            cv.create_image(2,2,image=card,anchor="nw"); self._thumb_photos.append(card)
            im=page.get("preview",page["img"]).copy(); im.thumbnail((TW-26,TH-42),Image.Resampling.LANCZOS)
            ph=ImageTk.PhotoImage(im); self._thumb_photos.append(ph)
            cv.create_image(TW//2,(TH-24)//2+3,image=ph)
            txt=f"{idx+1}"+("  •" if page.get("processing") else "")
            cv.create_text(TW//2,TH-15,text=txt,fill=T["accent_soft_fg"] if sel else T["fg2"],font=ui.F("small_b"))
            cv.bind("<Button-1>",lambda e,i=idx:self._select_thumbnail(i))
            cv.bind("<ButtonPress-3>",lambda e,i=idx:self._thumb_drag_start(i))
            cv.bind("<B3-Motion>",self._thumb_drag_motion)
            cv.bind("<ButtonRelease-3>",self._thumb_drag_end)
            cv.pack(side="right",padx=4,pady=0)
        self.thumb_inner.update_idletasks(); self._layout_thumbs()
        if self.pages:
            tc=self.thumb_canvas; iw=self.thumb_inner.winfo_reqwidth(); cw=tc.winfo_width()
            if iw>cw:
                kids=self.thumb_inner.winfo_children(); cx=kids[self.current].winfo_x()+TW/2
                tc.xview_moveto(max(0.0,min(1.0,(cx-cw/2)/iw)))

    # >>> fit_page
    def fit_page(self):
        self.zoom.set(100.0); self.compare_mode.set(False); self._pan=[0,0]; self.show_page()

    # >>> toggle_theme
    def toggle_theme(self):
        dark=ui.THEME_NAME!="dark"
        ui.set_theme("dark" if dark else "light")
        self.dark_mode.set(dark); self._save_prefs(); self._apply_theme()

    def _apply_theme(self):
        self.configure(bg=T["bg"]); self._style_scrollbar()
        self.canvas.configure(bg=T["canvas"],highlightbackground=T["border"],highlightcolor=T["border"])
        self.side_canvas.configure(bg=T["bg"])
        self.thumb_canvas.configure(bg=T["bg"]); self.thumb_inner.configure(bg=T["bg"])
        self.btn_theme.set_icon("sun" if ui.THEME_NAME=="dark" else "moon")
        ui.retheme_all()
        self.show_page()


    def _settings(self):
        return dict(mode=FILTERS[self.filter_name.get()],crop=self.opt_crop.get(),deskew=self.opt_deskew.get(),
                    shadows=self.opt_shadows.get(),denoise=self.opt_denoise.get(),dpi=int(self.dpi.get()),
                    ai=self.opt_ai.get(),ink=round(self.ink.get(),1),white=round(self.white.get(),1),
                    contrast=self.opt_contrast.get(),sharp=self.opt_sharp.get(),color=self.opt_color.get(),
                     clean_color=self.opt_clean_color.get(),trim=self.opt_trim.get(),dust=self.opt_dust.get(),
                     defringe=self.opt_defringe.get(),
                     brightness=round(self.brightness.get(),3),saturation=round(self.saturation.get(),3),
                     gamma=round(self.gamma.get(),3))

    def apply_preset(self):
        if self.pages and not self.working:self._push_history()
        p=FILTER_PRESETS[self.preset.get()]
        self.filter_name.set(next(k for k,v in FILTERS.items() if v==p["mode"]))
        self.opt_crop.set(p["crop"]); self.opt_deskew.set(p["deskew"]); self.opt_shadows.set(p["shadows"])
        self.opt_denoise.set(p["denoise"]); self.opt_contrast.set(p["contrast"]); self.opt_sharp.set(p["sharp"])
        self.opt_color.set(p["color"]); self.opt_ai.set(False)
        # الإعداد الجاهز لا يغيّر خيارات الضبط اليدوي ولا القص/الحواف
        if self.pages: self.reprocess()
        else: self.status.set("تم اختيار الإعداد الجاهز. سيُستخدم عند المسح التالي.")

    def scan(self):
        """Scan in a worker; publish the raw page before expensive enhancement."""
        if self.working:return
        st=self._settings()
        # مسح ملوّن من الماسح افتراضيًا: يتيح تغيير الفلتر لاحقًا دون إعادة المسح
        # ويحافظ على المعاينة الأصلية بالألوان. يمكن إيقافه من تبويب المسح.
        color=bool(self.scan_color.get()) or st["mode"] in ("magic","original")
        self._set_busy(True,"جارٍ الاتصال بالماسح الضوئي…")
        def worker():
            try:
                path=wia_scan(st["dpi"],color)
                # Prepare a quick preview immediately after WIA returns. The full-resolution
                # source remains untouched and is the basis for all later processing.
                raw=se.bgr_to_pil(se.load_bgr(path))
                raw_preview=raw.copy(); raw_preview.thumbnail((2200,2200),Image.Resampling.LANCZOS)
                page={"path":path,"rot":0,"manual_angle":0.0,"crop_norm":None,
                      "img":raw,"preview":raw_preview,"orig_preview":raw_preview.copy(),
                      "dpi":st["dpi"],"processing":True,"settings":st.copy()}
                published=threading.Event()
                def publish():
                    try:self._page_captured(page)
                    finally:published.set()
                self._post_ui(publish)
                # Wait until Tk has painted the original preview before starting CPU-heavy work.
                published.wait(timeout=5)
                self._process_page(page,st)
                self._post_ui(lambda:self._page_processed(page))
            except Exception as e:
                msg=str(e); self._post_ui(lambda:self._fail("فشل المسح أو المعالجة: "+msg))
        threading.Thread(target=worker,daemon=True).start()

    def _page_captured(self,page):
        self.pages.append(page); self.current=len(self.pages)-1
        self.show_original=True
        self.show_page()
        # Flush pending Tk drawing before releasing the worker to start heavy processing.
        self.update_idletasks()
        origin = "اكتمل المسح" if page.get("source_label") != "image" else "فُتحت الصورة"
        self.status.set(origin+" — تظهر النسخة الأصلية الآن، والتحسين جارٍ في الخلفية…")
        self.page_label.configure(text=f"الصفحة {self.current+1} / {len(self.pages)} — الأصل • جارٍ التحسين")
        self._save_session()

    def _page_processed(self,page):
        page["processing"]=False
        self._discard_page_temp(page)
        self._set_busy(False)
        if self.opt_skip_blank.get() and len(self.pages)>1 and any(e is page for e in self.pages) and extras.is_blank(page["img"],float(self.blank_sens.get())):
            idx=next(i for i,e in enumerate(self.pages) if e is page); del self.pages[idx]
            self.current=max(0,min(self.current,len(self.pages)-1)); self.show_page(); self._refresh_thumbnails(); self._save_session()
            self.status.set("تم تخطي صفحة فارغة تلقائيًا (يمكن إيقاف ذلك من تبويب أدوات).")
            return
        if any(existing is page for existing in self.pages) and self.pages[self.current] is page:
            self.show_original=False
            self.show_page()
        self._refresh_thumbnails(); self._schedule_estimate()
        w,h=page["img"].size
        self.status.set(f"اكتمل التحسين: {w:,} × {h:,} بكسل | ميلان تلقائي {page.get('auto_angle',0):+.2f}°"+
                        ((" | "+page["note"]) if page.get("note") else ""))
        self._save_session()

    def _fail(self,msg):
        self._set_busy(False,msg); messagebox.showerror("Canon Scan Pro",msg)

    def _post_manual(self,im,st):
        # Optional finishing tools, applied after the document filter.
        if st.get("clean_color",False) and im.mode not in ("1","L"):
            arr=cv2.cvtColor(np.array(im.convert("RGB")),cv2.COLOR_RGB2BGR)
            arr=se.clean_document_preserve_color(arr,1)
            im=Image.fromarray(cv2.cvtColor(arr,cv2.COLOR_BGR2RGB))
        brightness=float(st.get("brightness",0) or 0)
        saturation=float(st.get("saturation",0) or 0)
        gamma=float(st.get("gamma",1) or 1)
        if im.mode=="1": im=im.convert("L")
        if abs(brightness)>1e-3:
            im=ImageEnhance.Brightness(im).enhance(max(0.0,1.0+brightness))
        if abs(saturation)>1e-3 and im.mode not in ("1","L"):
            im=ImageEnhance.Color(im).enhance(max(0.0,1.0+saturation))
        if abs(gamma-1.0)>1e-3:
            im=im.point(lambda v: min(255,int(255*((v/255.0)**gamma)+0.5)))
        if st["contrast"]:
            im=ImageEnhance.Contrast(im).enhance(1.12)
        if st["color"] and im.mode not in ("1","L"):
            im=ImageEnhance.Color(im).enhance(1.18)
        if st["sharp"]:
            im=ImageEnhance.Sharpness(im).enhance(1.35)
            im=im.filter(ImageFilter.UnsharpMask(radius=1.2,percent=120,threshold=3))
        return im

    def _process_page(self,page,st):
        base=se.rotate_quarters(se.load_bgr(page["path"]),page["rot"])
        out,angle,scale,note=se.process(base,st["mode"],st["crop"],st["deskew"],st["shadows"],
                                       st["denoise"],st["dpi"],st["ai"],st["ink"],st["white"],
                                       trim=st.get("trim",True),dust=st.get("dust",True),
                                       defringe=st.get("defringe",True))
        out=self._post_manual(out,st)
        if page.get("manual_angle",0):
            # الإشارة الموحّدة: الزاوية الموجبة = عكس عقارب الساعة (مثل تصحيح الميلان التلقائي)
            out=out.rotate(float(page["manual_angle"]),expand=True,resample=Image.Resampling.BICUBIC,fillcolor="white")
        cr=page.get("crop_norm")
        if cr:
            w,h=out.size; x1,y1,x2,y2=cr
            box=(max(0,int(x1*w)),max(0,int(y1*h)),min(w,int(x2*w)),min(h,int(y2*h)))
            if box[2]>box[0]+10 and box[3]>box[1]+10: out=out.crop(box)
        preview=out.copy(); preview.thumbnail((2200,2200),Image.Resampling.LANCZOS)
        original=se.bgr_to_pil(se._shrink(base,2000)[0])
        page.update(img=out,preview=preview,orig_preview=original,mode=st["mode"],dpi=int(st["dpi"]*scale),
                    auto_angle=angle,note=note,settings=st.copy())

    def reprocess(self):
        if self.working:return
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة أولاً.")
        page=self.pages[self.current]; st=self._settings()
        page["processing"]=True
        self._set_busy(True,"جارٍ تطبيق التحسينات…")
        def worker():
            try:
                self._process_page(page,st)
                self._post_ui(lambda:self._page_ready(page,False))
            except Exception as e:
                msg=str(e); self._post_ui(lambda:self._fail("فشلت المعالجة: "+msg))
        threading.Thread(target=worker,daemon=True).start()

    def reprocess_all(self):
        """تطبيق الإعدادات الحالية على كل الصفحات دفعة واحدة."""
        if self.working:return
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة واحدة على الأقل.")
        st=self._settings(); pages=list(self.pages)
        self._set_busy(True,f"جارٍ تطبيق التحسينات على {len(pages)} صفحة…")
        def worker():
            done=[]
            try:
                for index,page in enumerate(pages):
                    self._process_page(page,st)
                    done.append((page,index))
                    self._post_ui(lambda i=index,p=len(pages): self.status.set(f"جارٍ المعالجة… {i+1} من {p}"))
                self._post_ui(lambda:self._pages_ready(done))
            except Exception as e:
                msg=str(e); self._post_ui(lambda:self._fail("فشلت المعالجة: "+msg))
        threading.Thread(target=worker,daemon=True).start()

    def _pages_ready(self,done):
        self._set_busy(False)
        for page,_index in done:
            page["processing"]=False
        self.show_original=False; self.compare_mode.set(False); self.show_page()
        self._refresh_thumbnails(); self._schedule_estimate(); self._save_session()
        if done:
            w,h=done[-1][0]["img"].size
            self.status.set(f"تم تطبيق التحسينات على {len(done)} صفحة | الحجم النهائي {w:,} × {h:,} بكسل")

    def _page_ready(self,page,is_new):
        if is_new:self.pages.append(page); self.current=len(self.pages)-1
        page["processing"]=False
        self._discard_page_temp(page)
        self.show_original=False; self._set_busy(False)
        self.show_page(); self._refresh_thumbnails(); self._schedule_estimate(); self._save_session()
        w,h=page["img"].size
        self.status.set(f"جاهز: {w:,} × {h:,} بكسل | ميلان تلقائي {page.get('auto_angle',0):+.2f}°"+((" | "+page["note"]) if page.get("note") else ""))

    def _on_resize(self,_e):
        if self._resize_job:
            try:self.after_cancel(self._resize_job)
            except Exception:pass
        self._resize_job=self.after(100,self.show_page)

    def _snapshot_page(self,page):
        # لا نحفظ orig_preview: يُعاد توليده من الملف الأصلي عند التراجع،
        # وهذا يوفّر نحو ثلث استهلاك الذاكرة في سجل التراجع.
        keys=("img","preview","settings","manual_angle","crop_norm","rot","dpi","mode","auto_angle","note")
        snap={}
        for key in keys:
            value=page.get(key)
            if key in ("img","preview") and value is not None:value=value.copy()
            elif key=="settings" and value is not None:value=dict(value)
            snap[key]=value
        return snap

    @staticmethod
    def _snapshot_bytes(snap):
        total=0
        for key in ("img","preview"):
            im=snap.get(key)
            if im is not None:
                pixels=int(im.width)*int(im.height)
                total+=pixels//8 if im.mode=="1" else pixels*len(im.getbands())
        return total

    def _trim_history(self):
        while self._history and (len(self._history)>HISTORY_MAX_STEPS or self._history_bytes>HISTORY_MAX_BYTES):
            _page,old=self._history.pop(0)
            self._history_bytes-=self._snapshot_bytes(old)
        self._history_bytes=max(0,self._history_bytes)

    def _push_history(self):
        if not self.pages or self.working:return
        page=self.pages[self.current]
        snap=self._snapshot_page(page)
        self._history.append((page,snap))
        self._history_bytes+=self._snapshot_bytes(snap)
        self._trim_history()
        self._redo.clear()

    def _apply_snapshot(self,page,snap):
        for key,value in snap.items():
            if key in ("img","preview") and value is not None:value=value.copy()
            elif key=="settings" and value is not None:value=dict(value)
            page[key]=value
        # إعادة توليد المعاينة الأصلية من الملف الأصلي (بدل تخزينها)
        if page.get("path"):
            try:page["orig_preview"]=se.original_preview(page["path"],page.get("rot",0))
            except Exception:pass
        self._sync_controls_from_settings(page.get("settings") or {})
        self.show_original=False; self.compare_mode.set(False); self.show_page()
        self._schedule_estimate(); self._save_session()

    def _sync_controls_from_settings(self,st):
        try:
            self.filter_name.set(next(k for k,v in FILTERS.items() if v==st.get("mode", "magic")))
            self.opt_crop.set(st.get("crop",True)); self.opt_deskew.set(st.get("deskew",True))
            self.opt_shadows.set(st.get("shadows",True)); self.opt_denoise.set(st.get("denoise",False))
            self.opt_ai.set(st.get("ai",False)); self.opt_contrast.set(st.get("contrast",True))
            self.opt_sharp.set(st.get("sharp",True)); self.opt_color.set(st.get("color",False))
            self.opt_clean_color.set(st.get("clean_color",True)); self.ink.set(st.get("ink",0)); self.white.set(st.get("white",50))
            self.opt_trim.set(st.get("trim",True)); self.opt_dust.set(st.get("dust",True))
            self.opt_defringe.set(st.get("defringe",True))
            self.brightness.set(st.get("brightness",0)); self.saturation.set(st.get("saturation",0))
            self.gamma.set(st.get("gamma",1))
        except Exception:pass

    def undo(self):
        if self.working:return
        if not self._history:return self.status.set("لا توجد عملية يمكن التراجع عنها في هذه الجلسة.")
        page,snap=self._history.pop()
        self._history_bytes-=self._snapshot_bytes(snap)
        if not any(pg is page for pg in self.pages):return
        self._redo.append((page,self._snapshot_page(page))); self._apply_snapshot(page,snap)
        self.status.set("تم التراجع عن آخر تعديل.")

    def redo(self):
        if self.working:return
        if not self._redo:return self.status.set("لا توجد عملية لإعادتها.")
        page,snap=self._redo.pop()
        if not any(pg is page for pg in self.pages):return
        current=self._snapshot_page(page)
        self._history.append((page,current))
        self._history_bytes+=self._snapshot_bytes(current)
        self._trim_history()
        self._apply_snapshot(page,snap)
        self.status.set("تمت إعادة التعديل.")

    def _select_thumbnail(self,index):
        self.current=index; self.show_original=False; self.compare_mode.set(False)
        self._reset_crop_selection(); self.show_page(); self._schedule_estimate()

    def _thumb_drag_start(self,index): self._drag_thumb=index
    def _thumb_drag_motion(self,event):
        if self._drag_thumb is not None:
            self.status.set("اسحب بزر الفأرة الأيمن ثم أفلت فوق شريط الصفحات لإعادة الترتيب.")
    def _thumb_drag_end(self,event):
        if self._drag_thumb is None:return
        try:
            x=self.thumb_canvas.canvasx(event.x_root-self.thumb_canvas.winfo_rootx())
            frames=list(self.thumb_inner.winfo_children())
            target=min(range(len(frames)),key=lambda i:abs(frames[i].winfo_x()+frames[i].winfo_width()/2-x)) if frames else self._drag_thumb
            src=self._drag_thumb
            if src != target:
                page=self.pages.pop(src); self.pages.insert(target,page)
                if self.current==src:self.current=target
                elif src<self.current<=target:self.current-=1
                elif target<=self.current<src:self.current+=1
                self.show_page(); self._save_session(); self.status.set("تم تغيير ترتيب الصفحات.")
        finally:self._drag_thumb=None

    def toggle_compare(self):
        if not self.pages:return
        self.compare_mode.set(not self.compare_mode.get()); self.show_original=False; self.show_page()

    def _save_session(self):
        """Persist the current session without ever overwriting a source page mid-copy.

        Reordering restored pages makes page_001… both a source and a destination.
        Copying in one pass would overwrite a source before it is read, so every
        changed file is first copied to a unique temp name and only then renamed.
        """
        if getattr(self,"_restoring",False):return
        temp_files=[]
        try:
            os.makedirs(self._session_dir,exist_ok=True); plans=[]
            for i,page in enumerate(self.pages):
                src=page.get("path")
                if not src or not os.path.exists(src): continue
                ext=os.path.splitext(src)[1] or ".png"
                stable=os.path.join(self._session_dir,f"page_{i+1:03d}{ext}")
                plans.append((page,src,stable))
            for page,src,stable in plans:
                if os.path.normcase(os.path.abspath(src))==os.path.normcase(os.path.abspath(stable)): continue
                tmp=os.path.join(self._session_dir,f".tmp_{uuid.uuid4().hex}{os.path.splitext(stable)[1]}")
                shutil.copy2(src,tmp); temp_files.append((tmp,stable))
            for tmp,stable in temp_files:
                os.replace(tmp,stable)
            for page,src,stable in plans:
                page["path"]=stable
                if self._is_temp_page(src):
                    if page.get("processing"):
                        page["_temp_source"]=src
                    else:
                        self._remove_temp_file(src)
            for page in self.pages:
                if not page.get("processing"):self._discard_page_temp(page)
            entries=[{"path":stable,"rot":page.get("rot",0),"manual_angle":page.get("manual_angle",0),
                      "crop_norm":page.get("crop_norm"),"dpi":page.get("dpi",300),
                      "settings":page.get("settings",self._settings())}
                     for page,_src,stable in plans]
            used=[os.path.basename(stable) for _page,_src,stable in plans]
            current=min(max(0,int(self.current)),max(0,len(entries)-1))
            tmp=os.path.join(self._session_dir,"session.tmp")
            with open(tmp,"w",encoding="utf-8") as f:json.dump({"pages":entries,"current":current},f,ensure_ascii=False)
            os.replace(tmp,os.path.join(self._session_dir,"session.json"))
            self._cleanup_session_files(used)
        except Exception:
            for tmp,_stable in temp_files:
                try:os.remove(tmp)
                except Exception:pass
            traceback.print_exc()

    @staticmethod
    def _is_temp_page(path):
        """هل المسار ملف مؤقت أنشأه التطبيق (وليس صورة المستخدم الأصلية)؟"""
        try:
            root=os.path.normcase(os.path.abspath(tempfile.gettempdir()))
            full=os.path.abspath(path)
            return (os.path.normcase(os.path.dirname(full))==root and
                    os.path.basename(full).startswith(("canon_scan_","csp_import_","csp_pdf_")))
        except Exception:
            return False

    @staticmethod
    def _remove_temp_file(path):
        try:
            if path and os.path.isfile(path):os.remove(path)
        except OSError:
            pass

    def _discard_page_temp(self,page):
        """يحذف المصدر المؤقت بعد أن أصبح للصفحة نسخة دائمة في مجلد الجلسة."""
        src=page.pop("_temp_source",None)
        if not src:return
        try:
            if os.path.normcase(os.path.abspath(src))!=os.path.normcase(os.path.abspath(page.get("path",src))):
                self._remove_temp_file(src)
        except Exception:
            pass

    def _cleanup_session_files(self,used):
        """Delete old page files that are no longer used by the session.

        Safety: never delete while a background restore is still reading the
        session files, and never delete when the used list is empty (the page
        list may only be temporarily empty during startup restore).
        """
        try:
            if getattr(self,"_restoring",False) or not used:return
            for name in os.listdir(self._session_dir):
                stale=(name.startswith("page_") and name not in used) or name.startswith(".tmp_")
                if stale:
                    try:os.remove(os.path.join(self._session_dir,name))
                    except Exception:pass
        except Exception:
            traceback.print_exc()

    def _reset_session_state(self):
        """يفرّغ الصفحات والسجل ويحذف ملفات الجلسة المحفوظة (مع الإبقاء على التفضيلات)."""
        self.pages=[]; self.current=0
        self._history.clear(); self._redo.clear(); self._history_bytes=0
        self.show_original=False; self.compare_mode.set(False)
        self._reset_crop_selection()
        try:
            for name in os.listdir(self._session_dir):
                if name.startswith("page_") or name in ("session.json","session.tmp"):
                    try:os.remove(os.path.join(self._session_dir,name))
                    except Exception:pass
        except Exception:pass
        self.show_page(); self._refresh_thumbnails(); self._schedule_estimate()
        self._save_session()

    def new_session(self):
        if self._restoring:return messagebox.showinfo("استعادة الجلسة","انتظر انتهاء استعادة الجلسة السابقة قبل بدء جلسة جديدة.")
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء المسح أو التحسين قبل بدء جلسة جديدة.")
        if self.pages and not messagebox.askyesno("جلسة جديدة","بدء جلسة جديدة وإزالة كل الصفحات الحالية؟\nلن تُحذف الملفات التي حفظتها على القرص."):return
        self._reset_session_state()
        self.status.set("تم بدء جلسة جديدة — ضع الورقة على الماسح ثم اضغط «بدء المسح الضوئي».")

    def _restore_session(self):
        file=os.path.join(self._session_dir,"session.json")
        if not os.path.isfile(file):return
        try:
            with open(file,encoding="utf-8") as f:data=json.load(f)
        except Exception:
            self.status.set("تعذرت قراءة الجلسة السابقة؛ يمكنك متابعة العمل بشكل الطبيعي."); return
        items=[item for item in data.get("pages",[]) if os.path.isfile(item.get("path",""))]
        if not items:return
        current=max(0,min(len(items)-1,int(data.get("current",0))))
        defaults=self._settings()
        self._restoring=True
        self.status.set("جارٍ استعادة جلسة العمل السابقة…")
        def worker():
            restored=[]
            try:
                for item in items:
                    try:
                        with Image.open(item["path"]) as source:
                            im=ImageOps.exif_transpose(source).convert("RGB")
                    except Exception:
                        continue          # ملف مفقود: نتخطاه بدل إفشال الاستعادة كلها
                    preview=im.copy(); preview.thumbnail((2200,2200),Image.Resampling.LANCZOS)
                    page={"path":item["path"],"rot":item.get("rot",0),"manual_angle":item.get("manual_angle",0),
                          "crop_norm":item.get("crop_norm"),"img":im,"preview":preview,"orig_preview":preview.copy(),
                          "dpi":item.get("dpi",300),"processing":True,
                          "settings":item.get("settings") or dict(defaults),"source_label":"restored"}
                    try:self._process_page(page,page["settings"])
                    except Exception:pass
                    restored.append(page)
                self._post_ui(lambda:self._session_restored(restored,current))
            except Exception:
                traceback.print_exc()
                self._post_ui(lambda:self._set_restoring(False))
        threading.Thread(target=worker,daemon=True).start()

    def _set_restoring(self,value):
        self._restoring=bool(value)

    def _session_restored(self,pages,current):
        self._restoring=False
        for page in pages: page["processing"]=False
        if self.pages:
            # المستخدم مسح صفحة أثناء الاستعادة: لا نفقدها.
            self.pages=self.pages+pages
            self.current=len(self.pages)-1
        else:
            self.pages=pages
            self.current=min(max(0,int(current)),max(0,len(pages)-1)) if pages else 0
        self.show_original=False; self.compare_mode.set(False)
        self.show_page(); self._refresh_thumbnails(); self._schedule_estimate(); self._save_session()
        self.status.set("تمت استعادة جلسة العمل السابقة تلقائيًا.")

    def move(self,step):
        if self.pages:
            self.current=max(0,min(len(self.pages)-1,self.current+step)); self._reset_crop_selection()
            self.show_original=False
            self.show_page(); self._schedule_estimate()

    def toggle_original(self):
        if self.pages:self.show_original=not self.show_original; self.show_page()

    def rotate_degrees(self,delta):
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة أولاً.")
        self._push_history()
        page=self.pages[self.current]
        # التوحيد على المدى (-180, 180] حتى لا تتحول زاوية -1° إلى 359°
        value=(float(page.get("manual_angle",0))+float(delta)+180.0)%360.0-180.0
        if abs(value+180.0)<1e-9:value=180.0
        page["manual_angle"]=value
        self.angle_entry.set(f"{value:.1f}")
        self.reprocess()

    def apply_angle(self):
        try:angle=float(self.angle_entry.get())
        except ValueError:return messagebox.showerror("زاوية غير صحيحة","أدخل زاوية رقمية مثل 1 أو -1 أو 0.5.")
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة أولاً.")
        self._push_history()
        angle=(angle+180.0)%360.0-180.0
        self.pages[self.current]["manual_angle"]=angle
        self.angle_entry.set(f"{angle:.1f}")
        self.reprocess()

    def _reset_crop_selection(self):
        """إلغاء أي تحديد قص قائم (عند تبديل الصفحة أو تغيير العرض أو بعد التطبيق)."""
        self._crop_start=None
        if hasattr(self,"_crop_pixels"):delattr(self,"_crop_pixels")
        if getattr(self,"_crop_rect_id",None):
            try:self.canvas.delete(self._crop_rect_id)
            except Exception:pass
        self._crop_rect_id=None
        try:self.canvas.configure(cursor="")
        except Exception:pass

    def start_crop(self):
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة أولاً.")
        self._crop_start=None
        self.canvas.configure(cursor="crosshair")
        self.status.set("اسحب بالماوس فوق الصفحة لتحديد مستطيل القص، ثم اضغط «قص التحديد».")

    def _crop_press(self,e):
        if not self.pages or not self._canvas_img_rect:return
        x1,y1,x2,y2=self._canvas_img_rect
        if x1<=e.x<=x2 and y1<=e.y<=y2:
            self._crop_start=(e.x,e.y)
            if self._crop_rect_id:self.canvas.delete(self._crop_rect_id)
            self._crop_rect_id=self.canvas.create_rectangle(e.x,e.y,e.x,e.y,outline=T["accent"],width=2,dash=(5,3))

    def _crop_drag(self,e):
        if self._crop_start and self._crop_rect_id:
            x1,y1,x2,y2=self._canvas_img_rect
            x=max(x1,min(x2,e.x)); y=max(y1,min(y2,e.y))
            self.canvas.coords(self._crop_rect_id,self._crop_start[0],self._crop_start[1],x,y)

    def _crop_release(self,e):
        if not self._crop_start or not self._canvas_img_rect:return
        x1,y1,x2,y2=self._canvas_img_rect
        x=max(x1,min(x2,e.x)); y=max(y1,min(y2,e.y))
        sx,sy=self._crop_start
        self._crop_pixels=(max(x1,min(sx,x)),max(y1,min(sy,y)),min(x2,max(sx,x)),min(y2,max(sy,y)))
        self.status.set("تم تحديد منطقة القص. اضغط «قص التحديد» لتطبيقها.")

    def apply_crop(self):
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة أولاً.")
        if not hasattr(self,"_crop_pixels"):return messagebox.showinfo("تحديد القص","اضغط «بدء تحديد القص» ثم اسحب على الصورة.")
        rect=self._canvas_img_rect
        if not rect:return
        x0,y0,x1,y1=self._crop_pixels; rx,ry,_,_=rect
        dispw=max(1,rect[2]-rect[0]); disph=max(1,rect[3]-rect[1])
        # Convert canvas coordinates to normalized page coordinates.
        cr=((x0-rx)/dispw,(y0-ry)/disph,(x1-rx)/dispw,(y1-ry)/disph)
        if cr[2]-cr[0]<.02 or cr[3]-cr[1]<.02:return messagebox.showwarning("قص صغير","حدد مساحة أكبر من ذلك.")
        self._push_history()
        page["crop_norm"]=cr
        self._reset_crop_selection()
        self.reprocess()

    def reset_crop(self):
        if not self.pages:return
        self._push_history()
        self.pages[self.current]["crop_norm"]=None
        self._reset_crop_selection()
        self.reprocess()

    def pick_folder(self):
        p=filedialog.askdirectory(initialdir=self.output_dir.get())
        if p:self.output_dir.set(p)

    def _prep(self,page):
        im=page["img"]
        if im.mode=="1":return im
        if im.mode=="L":return im
        return im.convert("RGB")

    # ------------------------------------------------------------- الترميز
    @staticmethod
    def _human(size):
        size=float(max(0,size))
        for unit in ("بايت","KB","MB","GB"):
            if size<1024 or unit=="GB":return f"{size:,.0f} {unit}" if unit=="بايت" else f"{size:,.1f} {unit}"
            size/=1024.0
        return f"{size:,.1f} GB"

    def _encode(self,im,fmt,quality):
        """ترميز الصورة بالصيغة المطلوبة وإرجاع البايتات (للحفظ أو للتقدير)."""
        buf=io.BytesIO()
        if fmt=="PNG":
            im.save(buf,format="PNG",optimize=True)
        elif fmt=="JPEG":
            im.convert("RGB").save(buf,format="JPEG",quality=quality,optimize=True,
                                   subsampling=1 if quality>=85 else 2)
        elif fmt=="WEBP":
            im.convert("RGB").save(buf,format="WEBP",quality=quality,method=4)
        elif fmt=="TIFF":
            im.convert("RGB").save(buf,format="TIFF",compression="tiff_deflate")
        else:
            im.convert("RGB").save(buf,format="BMP")
        return buf.getvalue()

    def _estimate_bytes(self,im,fmt,quality):
        """تقدير حجم الملف الناتج (تقريبي) بدون ضغط الصورة كاملة.

        تُضغط عيّنتان بأحجام مختلفة ثم يُستنتج الحجم النهائي وفق العلاقة
        bytes ≈ px^k التي تنطبق على خوارزميات الضغط (أدق من الاستخراج الخطي).
        """
        w,h=im.size
        if fmt=="BMP":                       # BMP 24-bit بدون ضغط
            stride=((w*3+3)//4)*4
            return stride*h+138
        points=[]
        for side in (480,960):
            sample=im.copy(); sample.thumbnail((side,side),Image.Resampling.LANCZOS)
            if sample.mode=="1":sample=sample.convert("L")
            try:data=self._encode(sample,fmt,quality)
            except Exception:return 0
            points.append((len(data),max(1,sample.size[0]*sample.size[1])))
        (b1,p1),(b2,p2)=points
        if b2<=b1 or p2<=p1:                 # الصورة أصغر من العيّنة
            return int(b1/float(p1)*w*h)
        try:k=math.log(b2/float(b1))/math.log(p2/float(p1))
        except ValueError:k=1.0
        k=max(0.3,min(1.6,k))
        return int(b2*((w*h)/float(p2))**k)

    def _cached_estimate(self,page,im,fmt,quality):
        key=(page.get("path"),fmt,quality,im.width,im.height)
        value=self._est_cache.get(key)
        if value is None:
            value=self._estimate_bytes(im,fmt,quality)
            if len(self._est_cache)>300:self._est_cache.clear()
            self._est_cache[key]=value
        return value

    def _schedule_estimate(self):
        if self._est_job:
            try:self.after_cancel(self._est_job)
            except Exception:pass
            self._est_job=None
        try:self._est_job=self.after(300,self._update_estimate)
        except Exception:pass

    def _update_estimate(self):
        """حسب التقدير في خيط منفصل حتى لا تتجمد الواجهة أثناء الضغط."""
        self._est_job=None
        fmt=self.out_format.get(); quality=max(1,min(100,int(self.out_quality.get())))
        try:self.fmt_hint.set(FORMAT_HINT.get(fmt,""))
        except Exception:pass
        if not self.pages:
            try:self.est_text.set("لا توجد صفحات بعد — امسح صفحة لعرض الحجم المتوقع.")
            except Exception:pass
            return
        if getattr(self,"_est_running",False):
            self._est_pending=True
            return
        self._est_running=True; self._est_pending=False
        pages=list(self.pages); current=self.current
        def worker():
            total=0; sizes={}; dims=""
            try:
                for page in pages:
                    im=page.get("img")
                    if im is None:continue
                    size=self._cached_estimate(page,im,fmt,quality)
                    sizes[id(page)]=size; total+=size
                im=pages[current].get("img") if current < len(pages) else None
                if im is not None:dims=f"{im.width:,} × {im.height:,} بكسل"
                self._post_ui(lambda:self._apply_estimate(fmt,quality,sizes,total,len(pages),dims))
            except Exception:
                self._post_ui(lambda:self._apply_estimate(fmt,quality,{},0,len(pages),""))
            finally:
                self._est_running=False
                if self._est_pending:
                    self._est_pending=False
                    self._post_ui(self._schedule_estimate)
        threading.Thread(target=worker,daemon=True).start()

    def _apply_estimate(self,fmt,quality,sizes,total,count,dims):
        try:
            if fmt!=self.out_format.get() or quality!=max(1,min(100,int(self.out_quality.get()))):return
        except Exception:return
        ext=EXPORT_FORMATS.get(fmt,(".png",True))[0]
        current_size=sizes.get(id(self.pages[self.current]),0) if self.pages and self.current < len(self.pages) else 0
        try:
            self.est_text.set(f"الصيغة: {fmt} ({ext})\n"
                              f"الصفحة الحالية ({dims or '—'}): ≈ {self._human(current_size)}\n"
                              f"كل الصفحات ({count}): ≈ {self._human(total)}")
        except Exception:pass

    def _save_as(self,im,path,fmt,quality=None,dpi=300):
        quality=max(1,min(100,int(self.out_quality.get() if quality is None else quality)))
        dpi=(int(dpi),int(dpi))
        if fmt=="JPEG":im.convert("RGB").save(path,format="JPEG",quality=quality,optimize=True,subsampling=1 if quality>=85 else 2,dpi=dpi)
        elif fmt=="WEBP":im.convert("RGB").save(path,format="WEBP",quality=quality,method=4,dpi=dpi)
        elif fmt=="TIFF":im.convert("RGB").save(path,format="TIFF",compression="tiff_deflate",dpi=dpi)
        elif fmt=="BMP":im.convert("RGB").save(path,format="BMP",dpi=dpi)
        else:im.save(path,format="PNG",optimize=True,dpi=dpi)

    def save_image(self):
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء التحسين قبل الحفظ حتى لا تحفظ النسخة الأولية بالخطأ.")
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة أولاً.")
        fmt=self.out_format.get(); ext=EXPORT_FORMATS.get(fmt,(".png",True))[0]
        output_dir=self.output_dir.get()
        os.makedirs(output_dir,exist_ok=True)
        page=self.pages[self.current]
        def choose(name):
            p=filedialog.asksaveasfilename(initialdir=output_dir,
                initialfile=name,defaultextension=ext,
                filetypes=[(f"{fmt} image",f"*{ext}"),("All files","*.*")])
            if not p:return
            try:
                self._save_as(self._prep(page),p,fmt,dpi=int(page.get("dpi",300)))
                self.status.set(f"تم حفظ الصورة ({fmt}) — الحجم الفعلي {self._human(os.path.getsize(p))}: {p}")
            except Exception as e:messagebox.showerror("تعذر الحفظ",str(e))
        self._suggest_name_async(ext,page,choose)

    def export_image(self):
        """زر «تصدير كصورة»: يحفظ الصفحة الحالية بالصيغة والجودة المختارتين."""
        return self.save_image()

    def save_pdf(self,current_only=False):
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء التحسين قبل تصدير PDF.")
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة واحدة على الأقل.")
        output_dir=self.output_dir.get()
        os.makedirs(output_dir,exist_ok=True)
        pages=[self.pages[self.current]] if current_only else list(self.pages)
        suffix=f"_page{self.current+1:03d}.pdf" if current_only else ".pdf"
        def choose(name):
            p=filedialog.asksaveasfilename(initialdir=output_dir,initialfile=name,
                defaultextension=".pdf",filetypes=[("PDF document","*.pdf")])
            if not p:return
            try:
                self._export_pdf(p,pages)
                if current_only:
                    self.status.set(f"تم تصدير الصفحة {self.current+1} من {len(self.pages)} إلى PDF ({self._human(os.path.getsize(p))}): {p}")
                else:
                    self.status.set(f"تم تصدير {len(pages)} صفحة إلى PDF ({self._human(os.path.getsize(p))}): {p}")
            except Exception as e:messagebox.showerror("تعذر حفظ PDF",str(e))
        self._suggest_name_async(suffix,pages[0],choose)

    @staticmethod
    def _fitz():
        try:
            import pymupdf as fitz
            return fitz
        except ImportError:
            try:
                import fitz
                return fitz
            except ImportError:
                return None

    def _export_pdf(self,path,pages=None):
        """تصدير الصفحات المختارة إلى PDF بمقاس صفحة قابل للاختيار مع بيانات وصفية."""
        pages=list(self.pages if pages is None else pages)
        if not pages:raise RuntimeError("لا توجد صفحات لتصديرها إلى PDF.")
        quality=max(1,min(100,int(self.pdf_quality.get())))
        fitz=self._fitz()
        if fitz is None:
            images=[self._prep(pg).convert("RGB") for pg in pages]
            images[0].save(path,save_all=True,append_images=images[1:],
                           resolution=float(pages[0].get("dpi",300)),quality=quality)
            return
        doc=fitz.open()
        size=PDF_PAGE_SIZES.get(self.pdf_page.get())
        for page in pages:
            im=self._prep(page).convert("RGB")
            dpi=max(72,int(page.get("dpi",300)))
            if size:
                pw,ph=size
            else:
                pw=im.width/float(dpi)*72.0; ph=im.height/float(dpi)*72.0
            pdf_page=doc.new_page(width=pw,height=ph)
            zoom=min(pw/im.width,ph/im.height)
            w=im.width*zoom; h=im.height*zoom
            rect=fitz.Rect((pw-w)/2.0,(ph-h)/2.0,(pw+w)/2.0,(ph+h)/2.0)
            pdf_page.insert_image(rect,stream=self._encode(im,"JPEG",quality))
        title=(self.pdf_title.get() or "Canon Scan Pro").strip()
        doc.set_metadata({"title":title,"author":"Canon Scan Pro","creator":"Canon Scan Pro",
                          "producer":"Canon Scan Pro","subject":f"{len(pages)} صفحة"})
        doc.save(path,garbage=4,deflate=True); doc.close()

    def save_multipage_tiff(self):
        """حفظ كل الصفحات في ملف TIFF واحد متعدد الصفحات."""
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء التحسين قبل التصدير.")
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة واحدة على الأقل.")
        os.makedirs(self.output_dir.get(),exist_ok=True)
        p=filedialog.asksaveasfilename(initialdir=self.output_dir.get(),
            initialfile=f"CanonScans_{datetime.now():%Y%m%d_%H%M%S}.tif",defaultextension=".tif",
            filetypes=[("TIFF image","*.tif *.tiff")])
        if not p:return
        try:
            images=[self._prep(pg).convert("RGB") for pg in self.pages]
            dpi=int(self.pages[0].get("dpi",300))
            images[0].save(p,save_all=True,append_images=images[1:],compression="tiff_deflate",dpi=(dpi,dpi))
            self.status.set(f"تم تصدير {len(images)} صفحة إلى TIFF متعدد الصفحات ({self._human(os.path.getsize(p))}): {p}")
        except Exception as e:messagebox.showerror("تعذر حفظ TIFF",str(e))

    def print_pages(self):
        """تجهيز PDF مؤقت ثم إرساله إلى الطابعة الافتراضية."""
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء التحسين قبل الطباعة.")
        if not self.pages:return messagebox.showinfo("تنبيه","امسح صفحة واحدة على الأقل.")
        if not hasattr(os,"startfile"):
            return messagebox.showinfo("الطباعة","الطباعة المباشرة متاحة على Windows فقط. استخدم تصدير PDF ثم اطبعه.")
        tmp=os.path.join(tempfile.gettempdir(),f"csp_print_{datetime.now():%Y%m%d_%H%M%S}.pdf")
        try:
            self._export_pdf(tmp)
            os.startfile(tmp,"print")
            self.status.set("تم إرسال المستند إلى قائمة الطباعة. راجع إعدادات الطابعة ثم وافق على الطباعة.")
        except Exception as e:
            messagebox.showerror("تعذر الطباعة",f"{e}\n\nالملف المؤقت موجود في:\n{tmp}")


    def open_image(self):
        """إضافة صورة موجودة كصفحة جديدة دون المساس بالملف الأصلي."""
        p=filedialog.askopenfilename(title="فتح صورة",filetypes=[("Images","*.png *.jpg *.jpeg *.tif *.tiff *.bmp"),("All files","*.*")])
        if not p:return
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء العملية الحالية قبل فتح صورة أخرى.")
        try:
            with Image.open(p) as source: im=ImageOps.exif_transpose(source).convert("RGB")
            tmp=os.path.join(tempfile.gettempdir(),f"csp_import_{datetime.now():%Y%m%d_%H%M%S_%f}.png")
            im.save(tmp)
            raw_preview=im.copy(); raw_preview.thumbnail((2200,2200),Image.Resampling.LANCZOS)
            page={"path":tmp,"rot":0,"manual_angle":0.0,"crop_norm":None,"img":im,
                  "preview":raw_preview,"orig_preview":raw_preview.copy(),"dpi":300,"processing":True,"source_label":"image"}
            st=self._settings(); st["mode"]="original"; st.update(crop=False,deskew=False,shadows=False,denoise=False,ai=False)
            self._set_busy(True,"تم فتح الصورة — جارٍ تجهيز المعاينة والتحسين…")
            self._page_captured(page)
            def worker():
                try:
                    self._process_page(page,st)
                    self._post_ui(lambda:self._page_processed(page))
                except Exception as e:
                    msg=str(e); self._post_ui(lambda:self._fail("تعذرت معالجة الصورة: "+msg))
            threading.Thread(target=worker,daemon=True).start()
        except Exception as e:messagebox.showerror("فتح الصورة",str(e))

    def _ocr_language(self,prefer=None):
        """Find Tesseract and validate actual traineddata files before OCR."""
        try:
            import pytesseract
        except ImportError:
            raise RuntimeError("مكتبة pytesseract غير مثبتة في بيئة Python الحالية. أعد بناء البرنامج بعد تثبيت المتطلبات.")

        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tesseract-OCR", "tesseract.exe"),
            os.path.join(os.environ.get("ProgramW6432", ""), "Tesseract-OCR", "tesseract.exe"),
            os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)), "tesseract", "tesseract.exe"),
        ]
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
            raise RuntimeError("لم أجد tesseract.exe. تحقق من وجوده داخل C:\\Program Files\\Tesseract-OCR\\tesseract.exe")

        pytesseract.pytesseract.tesseract_cmd = exe
        exe_dir = os.path.dirname(exe)
        tessdata_candidates = [
            os.path.join(exe_dir, "tessdata"),
            os.path.join(os.path.dirname(exe_dir), "share", "tessdata"),
            os.path.join(os.environ.get("TESSDATA_PREFIX", ""), "tessdata"),
            os.environ.get("TESSDATA_PREFIX", ""),
        ]
        tessdata = next((os.path.normpath(x) for x in tessdata_candidates
                         if x and os.path.isdir(x) and any(n.lower().endswith(".traineddata") for n in os.listdir(x))), None)
        if not tessdata:
            raise RuntimeError(
                "وجدت tesseract.exe لكن لم أجد ملفات *.traineddata.\n\n"
                "افتح C:\\Program Files\\Tesseract-OCR\\tessdata وتأكد من وجود ملفات ara.traineddata وfra.traineddata."
            )

        # The installed Tesseract already resolves its default tessdata directory correctly.
        # Passing --tessdata-dir through pytesseract on Windows can cause paths with spaces
        # to be parsed incorrectly in some packaged builds. Only set TESSDATA_PREFIX when
        # explicitly required; default to the install directory and let Tesseract resolve it.
        os.environ.pop("TESSDATA_PREFIX", None)
        td_config = ""
        files = {name.lower(): os.path.join(tessdata, name) for name in os.listdir(tessdata)}
        required = {"ara": "ara.traineddata", "fra": "fra.traineddata", "eng": "eng.traineddata"}
        missing = [f"{lang} ({filename})" for lang, filename in required.items()
                   if filename.lower() not in files or not os.path.isfile(files[filename.lower()]) or os.path.getsize(files[filename.lower()]) < 1024]
        if not any(os.path.isfile(files.get(required[lang].lower(), "")) and os.path.getsize(files[required[lang].lower()]) >= 1024
                   for lang in ("ara", "fra", "eng")):
            raise RuntimeError(
                f"مجلد tessdata موجود لكنه لا يحتوي ملفات لغة صالحة: {tessdata}\n\n"
                "نزّل ara.traineddata وfra.traineddata من مستودع tessdata الرسمي وضعهما داخل هذا المجلد."
            )

        try:
            langs = set(pytesseract.get_languages(config=td_config))
        except Exception as exc:
            detail = str(exc)
            raise RuntimeError(
                "تعذر على Tesseract قراءة ملفات اللغة. غالبًا أن ملف traineddata ناقص/تالف أو أن المجلد غير صحيح.\n\n"
                f"المسار المستخدم: {tessdata}\n"
                f"ملفات اللغات التي لم تُعثر عليها أو حجمها غير طبيعي: {', '.join(missing) or 'لم يُحدد'}\n\n"
                f"تفاصيل Tesseract: {detail}"
            )

        chosen = [x for x in ("ara", "fra", "eng") if x in langs]
        if not any(x in chosen for x in ("ara", "fra")):
            raise RuntimeError(
                f"تم تشغيل Tesseract لكن لم تُكتشف العربية أو الفرنسية.\nالمجلد: {tessdata}\n"
                f"اللغات المكتشفة: {', '.join(sorted(langs))}\n"
                "تأكد من وجود ara.traineddata وfra.traineddata داخل المجلد أعلاه."
            )
        if prefer:                       # لغة محددة من الواجهة
            wanted=[x for x in str(prefer).split("+") if x in langs]
            if wanted:return pytesseract,"+".join(wanted),td_config
        return pytesseract, "+".join(chosen), td_config

    def _selected_ocr_lang(self):
        return OCR_LANG_CHOICES.get(self.ocr_lang.get())

    def _tesseract_ready(self):
        """يتحقق من وجود Tesseract وملفات اللغة الصالحة دون إثارة استثناء."""
        try:
            self._ocr_language()
            return True
        except Exception:
            return False

    def _resolve_ocr_engine(self, mode=None):
        """يحدد المحرك الفعلي وفق اختيار المستخدم وتوفر المكتبات."""
        if mode is None:
            mode = OCR_ENGINE_CHOICES.get(self.ocr_engine.get(), "auto")
        rapid_ok = se.rapidocr_available()
        if mode == "rapidocr":
            if not rapid_ok:
                raise RuntimeError(
                    "RapidOCR غير مثبت. شغّل:\n  pip install rapidocr-onnxruntime onnxruntime\n"
                    "أو اختر محرك Tesseract من قائمة «محرك التعرف»."
                )
            return "rapidocr"
        if mode == "tesseract":
            return "tesseract"
        if self._tesseract_ready() or not rapid_ok:
            return "tesseract"
        return "rapidocr"

    def _ocr_page_text(self, im, engine, prefer, tess=None):
        if engine == "rapidocr":
            return se.rapidocr_text(im), "RapidOCR"
        pytesseract, lang, td_config = tess or self._ocr_language(prefer)
        return pytesseract.image_to_string(im, lang=lang, config=td_config + " --psm 3"), lang

    def export_ocr_text(self):
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء التحسين قبل تشغيل OCR.")
        if not self.pages:return messagebox.showinfo("OCR","أضف أو امسح صفحة أولاً.")
        os.makedirs(self.output_dir.get(),exist_ok=True)
        p=filedialog.asksaveasfilename(initialdir=self.output_dir.get(),initialfile=f"OCR_{datetime.now():%Y%m%d_%H%M%S}.txt",defaultextension=".txt",filetypes=[("Text UTF-8","*.txt")])
        if not p:return
        prefer=self._selected_ocr_lang(); pages=list(self.pages)
        engine_mode=OCR_ENGINE_CHOICES.get(self.ocr_engine.get(), "auto")
        self._set_busy(True,"جارٍ التعرّف على النص…")
        def worker():
            try:
                engine=self._resolve_ocr_engine(engine_mode)
                tess=self._ocr_language(prefer) if engine=="tesseract" else None
                chunks=[]; engine_label="OCR"
                for i,page in enumerate(pages,1):
                    im=self._prep(page).convert("RGB")
                    text,engine_label=self._ocr_page_text(im,engine,prefer,tess)
                    chunks.append(f"--- الصفحة {i} ---\n"+text)
                    self._post_ui(lambda i=i: self.status.set(f"جارٍ التعرّف على النص… الصفحة {i} من {len(pages)}"))
                with open(p,"w",encoding="utf-8-sig",newline="\n") as f:f.write("\n\n".join(chunks))
                self._post_ui(lambda:self._ocr_finished(f"اكتمل OCR بمحرك {engine_label} وحُفظ النص.",engine_label,p))
            except RuntimeError as e:
                msg=str(e); self._post_ui(lambda:self._ocr_failed("OCR غير جاهز",msg))
            except Exception as e:
                msg=str(e); self._post_ui(lambda:self._ocr_failed("فشل OCR",msg))
        threading.Thread(target=worker,daemon=True).start()

    # ---------------------------------------------------------- استخراج الفواتير
    def extract_invoices(self):
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء التحسين قبل استخراج الفاتورة.")
        if not self.pages:return messagebox.showinfo("استخراج الفاتورة","أضف أو امسح صفحة أولاً.")
        prefer=self._selected_ocr_lang(); pages=list(self.pages); each=self.inv_each_page.get()
        engine_mode=OCR_ENGINE_CHOICES.get(self.ocr_engine.get(),"auto")
        self._set_busy(True,"جارٍ قراءة الفاتورة…")
        def worker():
            try:
                engine=self._resolve_ocr_engine(engine_mode)
                tess=self._ocr_language(prefer) if engine=="tesseract" else None
                groups=[[i] for i in range(len(pages))] if each else [list(range(len(pages)))]
                results,previews=[],[]
                for gi,group in enumerate(groups,1):
                    def say(m,gi=gi):self._post_ui(lambda:self.status.set(f"الفاتورة {gi} من {len(groups)}: {m}"))
                    texts,res=[],None
                    for i in group:
                        im=self._prep(pages[i]).convert("RGB")
                        if engine=="rapidocr":
                            txt=se.rapidocr_text(im); res=ie.extract_invoice(txt); res["text"]=ie.best_text(txt)
                        else:
                            pt,lang,td=tess
                            res=ie.extract_from_image(im,pt,lang,td,progress=say)
                        texts.append(res["text"])
                    if len(group)>1:                       # فاتورة متعددة الصفحات: ندمج نصوصها
                        res=ie.extract_invoice("\n".join(texts)); res["text"]="\n".join(texts)
                    res["source"]=(f"صفحة {group[0]+1}" if len(group)==1 else f"الصفحات {group[0]+1}–{group[-1]+1}")
                    results.append(res); previews.append(pages[group[0]].get("preview",pages[group[0]]["img"]))
                self._post_ui(lambda:self._invoices_ready(results,previews))
            except RuntimeError as e:
                msg=str(e); self._post_ui(lambda:self._ocr_failed("OCR غير جاهز",msg))
            except Exception as e:
                msg=str(e); self._post_ui(lambda:self._ocr_failed("فشل استخراج الفاتورة",msg))
        threading.Thread(target=worker,daemon=True).start()

    def _invoices_ready(self,results,previews):
        self._set_busy(False)
        n=len(results); bad=sum(1 for r in results if any(l in("error","warn") for l,_,_ in r["warnings"]))
        self.status.set(f"اكتملت قراءة {n} فاتورة — {bad} تحتاج مراجعة." if bad else f"اكتملت قراءة {n} فاتورة بلا تنبيهات.")
        prefs=self._load_prefs()
        invoice_ui.InvoiceDialog(self,results,previews,{"url":prefs.get("erp_url",""),"token":prefs.get("erp_token","")},self._save_erp_cfg,self.output_dir.get())

    def _ocr_finished(self,message,lang,path):
        self._set_busy(False)
        self.status.set(message)
        messagebox.showinfo("اكتمل OCR",f"تم استخراج النص وحفظه في:\n{path}\n\nالرجاء مراجعة الأرقام والمبالغ يدويًا.")

    def _ocr_failed(self,title,msg):
        self._set_busy(False,msg)
        messagebox.showerror(title,msg)

    @staticmethod
    def _arabic_font_path():
        for name in ("segoeui.ttf", "tahoma.ttf", "arial.ttf"):
            path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
            if os.path.isfile(path):
                return path
        return None

    def _add_rapid_lines_to_page(self, page, im, lines, page_rect=None, scale=1.0):
        """يضيف طبقة نص غير مرئية (قابلة للبحث) من نتائج RapidOCR إلى صفحة PDF.

        ترجمات RapidOCR بإحداثيات بكسل الصورة؛ page_rect/scale يحوّلانها إلى
        إحداثيات صفحة PDF عند ملاءمة الصورة داخل مقاس مختار.
        """
        fitz = self._fitz()
        if fitz is None:
            return
        ox = page_rect.x0 if page_rect is not None else 0.0
        oy = page_rect.y0 if page_rect is not None else 0.0
        font = self._arabic_font_path()
        for box, text, _score in lines:
            if not text.strip():
                continue
            xs = [ox + float(p[0]) * scale for p in box]
            ys = [oy + float(p[1]) * scale for p in box]
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            if rect.width <= 1 or rect.height <= 1:
                continue
            base_fs = max(4.0, rect.height * 0.75)
            placed = False
            for scale in (1.0, 0.8, 0.65, 0.5, 0.4):
                fs = max(3.5, base_fs * scale)
                try:
                    if font:
                        rv = page.insert_textbox(rect, text, fontsize=fs, fontfile=font,
                                                 fontname="arb", render_mode=3,
                                                 align=fitz.TEXT_ALIGN_LEFT)
                    else:
                        rv = page.insert_textbox(rect, text, fontsize=fs, render_mode=3,
                                                 align=fitz.TEXT_ALIGN_LEFT)
                    if rv >= 0:
                        placed = True
                        break
                except Exception:
                    pass
            if not placed:
                try:
                    if font:
                        page.insert_text((rect.x0, rect.y1), text, fontsize=6,
                                         fontfile=font, fontname="arb", render_mode=3)
                    else:
                        page.insert_text((rect.x0, rect.y1), text, fontsize=6, render_mode=3)
                except Exception:
                    pass

    def save_searchable_pdf(self):
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء التحسين قبل إنشاء PDF قابل للبحث.")
        if not self.pages:return messagebox.showinfo("PDF قابل للبحث","أضف أو امسح صفحة أولاً.")
        os.makedirs(self.output_dir.get(),exist_ok=True)
        p=filedialog.asksaveasfilename(initialdir=self.output_dir.get(),initialfile=f"CanonScans_Searchable_{datetime.now():%Y%m%d_%H%M%S}.pdf",defaultextension=".pdf",filetypes=[("PDF document","*.pdf")])
        if not p:return
        prefer=self._selected_ocr_lang(); pages=list(self.pages)
        pdf_quality=int(self.pdf_quality.get())
        pdf_size=PDF_PAGE_SIZES.get(self.pdf_page.get())
        engine_mode=OCR_ENGINE_CHOICES.get(self.ocr_engine.get(), "auto")
        self._set_busy(True,"جارٍ إنشاء PDF قابل للبحث…")
        def worker():
            try:
                fitz=self._fitz()
                if fitz is None:raise ImportError("PyMuPDF")
                engine=self._resolve_ocr_engine(engine_mode)
                tess=self._ocr_language(prefer) if engine=="tesseract" else None
                merged=fitz.open()
                for i,page in enumerate(pages,1):
                    im=self._prep(page).convert("RGB")
                    if engine=="rapidocr":
                        lines=se.rapidocr_lines(im)
                        dpi=max(72,int(page.get("dpi",300)))
                        if pdf_size:pw,ph=pdf_size
                        else:pw=im.width/float(dpi)*72.0; ph=im.height/float(dpi)*72.0
                        pdf_page=merged.new_page(width=pw,height=ph)
                        zoom=min(pw/im.width,ph/im.height)
                        rw,rh=im.width*zoom,im.height*zoom
                        img_rect=fitz.Rect((pw-rw)/2.0,(ph-rh)/2.0,(pw+rw)/2.0,(ph+rh)/2.0)
                        buf=io.BytesIO(); im.save(buf,format="JPEG",quality=pdf_quality); buf.seek(0)
                        pdf_page.insert_image(img_rect,stream=buf.getvalue())
                        self._add_rapid_lines_to_page(pdf_page,im,lines,img_rect,zoom)
                    else:
                        pytesseract,lang,td_config=tess
                        pdf_bytes=pytesseract.image_to_pdf_or_hocr(im,extension="pdf",lang=lang,config=td_config+" --psm 3")
                        one=fitz.open(stream=pdf_bytes,filetype="pdf")
                        merged.insert_pdf(one); one.close()
                    self._post_ui(lambda i=i: self.status.set(f"جارٍ إنشاء PDF قابل للبحث… الصفحة {i} من {len(pages)}"))
                merged.save(p,garbage=4,deflate=True); merged.close()
                label="RapidOCR" if engine=="rapidocr" else "Tesseract"
                self._post_ui(lambda:self._ocr_finished(f"تم إنشاء PDF قابل للبحث ({label}).",label,p))
            except RuntimeError as e:
                msg=str(e); self._post_ui(lambda:self._ocr_failed("OCR غير جاهز",msg))
            except ImportError:
                self._post_ui(lambda:self._ocr_failed("مكتبة ناقصة","تحتاج PyMuPDF وpytesseract. راجع README_AR.md."))
            except Exception as e:
                msg=str(e); self._post_ui(lambda:self._ocr_failed("فشل إنشاء PDF قابل للبحث",msg))
        threading.Thread(target=worker,daemon=True).start()

    # ------------------------------------------------------------ ميزات إضافية
    def _suggest_name(self,ext,line=None):
        """اسم ملف مقترح: بادئة + (أول سطر OCR اختياريًا) + التاريخ."""
        return extras.smart_name(self.name_prefix.get().strip() or "Scan",line,self.name_date.get(),ext=ext)

    def _suggest_name_async(self,ext,page,callback):
        """اقتراح اسم الملف دون تجميد الواجهة عند تفعيل OCR."""
        if not self.name_ocr.get() or page is None:
            callback(self._suggest_name(ext)); return
        prefix=self.name_prefix.get().strip() or "Scan"
        use_date=bool(self.name_date.get())
        self._set_busy(True,"جارٍ قراءة العنوان لاقتراح اسم الملف…")
        def worker():
            line=None
            try:
                pytesseract,lang,_cfg=self._ocr_language()
                line=extras.first_text_line(self._prep(page),pytesseract,lang)
            except Exception:
                pass
            name=extras.smart_name(prefix,line,use_date,ext=ext)
            def done():
                self._set_busy(False)
                callback(name)
            self._post_ui(done)
        threading.Thread(target=worker,daemon=True).start()

    def remove_blank_pages(self):
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء المعالجة أولاً.")
        if not self.pages:return messagebox.showinfo("تنبيه","لا توجد صفحات.")
        sens=float(self.blank_sens.get())
        blanks=[i for i,pg in enumerate(self.pages) if extras.is_blank(pg["img"],sens)]
        if not blanks:return messagebox.showinfo("الصفحات الفارغة","لم يُعثر على صفحات فارغة بهذه الحساسية.")
        if len(blanks)>=len(self.pages):
            return messagebox.showinfo("الصفحات الفارغة","كل الصفحات تبدو فارغة؛ لم يُحذف شيء. قلّل الحساسية إن كان ذلك غير صحيح.")
        nums="، ".join(str(i+1) for i in blanks)
        if not messagebox.askyesno("حذف الصفحات الفارغة",f"عُثر على {len(blanks)} صفحة فارغة: {nums}\nهل تريد إزالتها من المستند؟"):return
        for i in reversed(blanks):del self.pages[i]
        self.current=max(0,min(self.current,len(self.pages)-1))
        self.show_page(); self._refresh_thumbnails(); self._schedule_estimate(); self._save_session()
        self.status.set(f"تمت إزالة {len(blanks)} صفحة فارغة (كان ترتيبها: {nums}).")

    def import_pdf(self):
        """فتح ملف PDF موجود وإضافة صفحاته كصور قابلة للتحرير والتصدير."""
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء العملية الحالية.")
        p=filedialog.askopenfilename(title="فتح PDF كصفحات",filetypes=[("PDF","*.pdf")])
        if not p:return
        pw=None
        try:
            try:n=extras.pdf_page_count(p)
            except ValueError:
                pw=simpledialog.askstring("PDF محميّ","كلمة مرور الملف:",show="*",parent=self)
                if not pw:return
                n=extras.pdf_page_count(p,pw)
        except Exception as e:return messagebox.showerror("فتح PDF",str(e))
        if n>60 and not messagebox.askyesno("ملف كبير",f"الملف يحوي {n} صفحة وقد يستهلك ذاكرة كبيرة. المتابعة؟"):return
        self._set_busy(True,f"جارٍ استيراد {n} صفحة من PDF…")
        def added(pg,k):
            self.pages.append(pg); self.current=len(self.pages)-1; self.show_original=False
            self.show_page(); self._refresh_thumbnails(); self.status.set(f"استُوردت الصفحة {k} من {n}…")
        def finish():
            self._set_busy(False); self._schedule_estimate(); self._save_session()
            self.status.set(f"اكتمل استيراد {n} صفحة من PDF.")
        def worker():
            try:
                st=self._settings(); st["mode"]="original"; st.update(crop=False,deskew=False,shadows=False,denoise=False,ai=False)
                for num,im in extras.render_pdf_pages(p,200,pw):
                    tmp=os.path.join(tempfile.gettempdir(),f"csp_pdf_{datetime.now():%Y%m%d_%H%M%S_%f}.png"); im.save(tmp)
                    pv=im.copy(); pv.thumbnail((2200,2200),Image.Resampling.LANCZOS)
                    page={"path":tmp,"rot":0,"manual_angle":0.0,"crop_norm":None,"img":im,"preview":pv,
                          "orig_preview":pv.copy(),"dpi":200,"processing":False,"source_label":"image"}
                    self._process_page(page,dict(st,dpi=200))
                    self._post_ui(lambda pg=page,k=num:added(pg,k))
                self._post_ui(finish)
            except Exception as e:
                msg=str(e); self._post_ui(lambda:self._fail("تعذر استيراد PDF: "+msg))
        threading.Thread(target=worker,daemon=True).start()

    # ---- أدوات PDF الخارجية
    def _pick_pdf(self,title="اختر ملف PDF"):
        return filedialog.askopenfilename(title=title,filetypes=[("PDF","*.pdf")])

    def _save_pdf_dialog(self,suffix):
        os.makedirs(self.output_dir.get(),exist_ok=True)
        return filedialog.asksaveasfilename(initialdir=self.output_dir.get(),initialfile=self._suggest_name(f"_{suffix}.pdf"),
                                            defaultextension=".pdf",filetypes=[("PDF","*.pdf")])

    def _pdf_done(self,msg,path=None):
        self.status.set(msg+(": "+path if path else ""))
        messagebox.showinfo("أدوات PDF",msg+(("\n"+path) if path else ""))

    def _pdf_guard(self,fn):
        try:fn()
        except ValueError as e:messagebox.showerror("أدوات PDF",str(e))
        except Exception as e:messagebox.showerror("أدوات PDF","تعذرت العملية: "+str(e))

    def tool_merge(self):
        def run():
            ps=filedialog.askopenfilenames(title="اختر ملفات PDF بالترتيب المطلوب",filetypes=[("PDF","*.pdf")])
            if not ps:return
            out=self._save_pdf_dialog("merged")
            if not out:return
            extras.merge_pdfs(list(ps),out); self._pdf_done(f"تم دمج {len(ps)} ملفات",out)
        self._pdf_guard(run)

    def tool_split(self):
        def run():
            src=self._pick_pdf()
            if not src:return
            n=extras.pdf_page_count(src)
            spec=simpledialog.askstring("تقسيم PDF",f"الملف فيه {n} صفحة.\nاكتب نطاقات كل ملف مفصولة بفاصلة (مثال: 1-3,4-6)\nأو اكتب كل:3 لتقسيم كل 3 صفحات.",parent=self)
            if not spec:return
            d=filedialog.askdirectory(initialdir=self.output_dir.get(),title="مجلد حفظ الأجزاء")
            if not d:return
            m=re.fullmatch(r"\s*(?:كل|every)\s*[:：]\s*(\d+)\s*",spec.translate(extras._AR_DIGITS),re.I)
            files=extras.split_pdf(src,d,every=int(m.group(1))) if m else extras.split_pdf(src,d,spec=spec)
            self._pdf_done(f"تم التقسيم إلى {len(files)} ملف",d)
        self._pdf_guard(run)

    def _range_tool(self,title,prompt,fn,suffix,done):
        def run():
            src=self._pick_pdf()
            if not src:return
            n=extras.pdf_page_count(src)
            spec=simpledialog.askstring(title,f"الملف فيه {n} صفحة.\n{prompt} (مثال: 1-3,5,8-)",parent=self)
            if not spec:return
            out=self._save_pdf_dialog(suffix)
            if not out:return
            fn(src,out,spec); self._pdf_done(done,out)
        self._pdf_guard(run)

    def tool_extract(self):self._range_tool("استخراج صفحات","اكتب الصفحات المطلوب استخراجها",extras.extract_pages,"extract","تم استخراج الصفحات")
    def tool_delete(self):self._range_tool("حذف صفحات","اكتب الصفحات المطلوب حذفها",extras.delete_pages,"edited","تم حذف الصفحات وحفظ نسخة جديدة")

    def tool_rotate(self):
        def run():
            src=self._pick_pdf()
            if not src:return
            n=extras.pdf_page_count(src)
            ang=simpledialog.askstring("تدوير صفحات","الزاوية: 90 أو 180 أو 270 (أو -90 للعكس)",initialvalue="90",parent=self)
            if not ang:return
            try:angle=int(ang.translate(extras._AR_DIGITS).strip())
            except ValueError:raise ValueError("الزاوية غير صحيحة.")
            spec=simpledialog.askstring("تدوير صفحات",f"الملف فيه {n} صفحة.\nالصفحات المطلوبة (اتركها فارغة لكل الصفحات)",parent=self)
            out=self._save_pdf_dialog("rotated")
            if not out:return
            extras.rotate_pages(src,out,angle,(spec or "").strip() or None); self._pdf_done("تم تدوير الصفحات",out)
        self._pdf_guard(run)

    def tool_protect(self):
        def run():
            src=self._pick_pdf()
            if not src:return
            pw1=simpledialog.askstring("حماية PDF","كلمة المرور (4 أحرف على الأقل):",show="*",parent=self)
            if not pw1:return
            pw2=simpledialog.askstring("حماية PDF","أعد كتابة كلمة المرور:",show="*",parent=self)
            if pw1!=pw2:raise ValueError("كلمتا المرور غير متطابقتين.")
            out=self._save_pdf_dialog("protected")
            if not out:return
            extras.protect_pdf(src,out,pw1); self._pdf_done("تمت حماية الملف بتشفير AES-256. احتفظ بكلمة المرور؛ لا يمكن استرجاعها",out)
        self._pdf_guard(run)

    def tool_compress(self):
        def run():
            src=self._pick_pdf()
            if not src:return
            out=self._save_pdf_dialog("compressed")
            if not out:return
            self.status.set("جارٍ ضغط الملف…"); self.update_idletasks()
            before,after=extras.compress_pdf(src,out)
            pct=(1-after/before)*100 if before else 0
            self._pdf_done(f"الحجم: {self._human(before)} ← {self._human(after)} ({pct:.0f}% توفير)",out)
        self._pdf_guard(run)

    def delete_page(self):
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء المسح أو التحسين قبل حذف الصفحة.")
        if not self.pages:return
        if not messagebox.askyesno("تأكيد","هل تريد إزالة الصفحة الحالية من قائمة المستند؟"):return
        del self.pages[self.current]; self.current=max(0,min(self.current,len(self.pages)-1)); self.show_page(); self._schedule_estimate(); self._save_session()
        self.status.set("تمت إزالة الصفحة من قائمة التصدير. الملف الأصلي المؤقت لم يُحذف.")

    def clear_pages(self):
        if self._restoring:return messagebox.showinfo("استعادة الجلسة","انتظر انتهاء استعادة الجلسة السابقة قبل إفراغ الصفحات.")
        if self.working:return messagebox.showinfo("المعالجة جارية","انتظر انتهاء المسح أو التحسين قبل إفراغ الصفحات.")
        if self.pages and messagebox.askyesno("تأكيد","إزالة جميع الصفحات من الجلسة الحالية؟"):
            self._reset_session_state()
            self.status.set("تم إفراغ قائمة الصفحات.")

def _selftest(out_path):
    """فحص ذاتي يُشغَّل من سكربت البناء: يتأكد أن كل المكتبات والنماذج داخل الحزمة تعمل فعلًا."""
    lines=[]; state={"ok":True}
    def chk(name,fn,fatal=True):
        try: lines.append(f"OK    {name}: {fn()}")
        except Exception as e:
            lines.append(f"{'FAIL' if fatal else 'WARN'}  {name}: {e!r}")
            if fatal: state["ok"]=False
    chk("cv2",lambda:cv2.__version__)
    chk("Pillow",lambda:__import__("PIL").__version__)
    chk("numpy",lambda:np.__version__)
    chk("scikit-image",lambda:__import__("skimage").__version__)
    chk("scipy",lambda:__import__("scipy").__version__)
    chk("PyWavelets",lambda:__import__("pywt").__version__)
    chk("wavelet denoise",lambda:(__import__("skimage.restoration",fromlist=["x"]).denoise_wavelet(np.random.rand(64,64).astype("float32"),method="BayesShrink",mode="soft",wavelet="db2",rescale_sigma=True,channel_axis=None).shape))
    chk("PyMuPDF",lambda:".".join(map(str,__import__("pymupdf").version[:1]))+" (pymupdf)")
    chk("pytesseract",lambda:(__import__("pytesseract"),"imported")[1])
    if sys.platform.startswith("win"):
        chk("win32com (WIA)",lambda:(__import__("win32com.client"),"imported")[1])
    chk("icon",lambda:app_icon.make_icon(64).size)
    def pipeline():
        im=np.full((700,500,3),255,np.uint8); cv2.putText(im,"Scan 123",(40,200),cv2.FONT_HERSHEY_SIMPLEX,2,(0,0,0),4)
        res=se.process(im,"magic",True,True,True,True,300,False,0,50); return res[0].size
    chk("enhance pipeline",pipeline)
    def extras_check():
        from PIL import Image as _I
        assert extras.is_blank(_I.new("RGB",(400,500),"white"))
        d=extras.fitz.open(); d.new_page(); d.new_page()
        tmp=os.path.join(tempfile.gettempdir(),"csp_selftest.pdf"); d.save(tmp); d.close()
        return f"blank detection + PDF tools ({extras.pdf_page_count(tmp)} pages)"
    chk("extras (blank/PDF tools)",extras_check)
    def pipeline_bw():
        im=np.full((700,500,3),255,np.uint8); cv2.putText(im,"Scan 123",(40,200),cv2.FONT_HERSHEY_SIMPLEX,2,(0,0,0),4)
        return se.process(im,"bw",True,True,True,False,300,False,0,50)[0].size
    chk("enhance pipeline (B/W)",pipeline_bw)
    def sr():
        if not se.ai_available(): raise RuntimeError("opencv-contrib dnn_superres missing")
        se.get_sr(); return "ESPCN model loaded"
    chk("AI super-resolution model",sr)
    def tess():
        for c in (r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe"):
            if os.path.isfile(c): return c
        found=shutil.which("tesseract")
        if found: return found
        raise RuntimeError("Tesseract not installed on this machine (the installer adds it)")
    def inv():
        r=ie.extract_invoice("SARL TEST\nFACTURE N° 2026/001\nLe 01/02/2026\nTotal HT 1 000,00\nTVA 19% 190,00\nTotal TTC 1 190,00")
        assert r["fields"]["ttc"]==1190.0 and r["fields"]["invoice_number"]=="2026/001" and not r["warnings"], r["warnings"]
        out=os.path.join(tempfile.gettempdir(),"csp_selftest_inv.xlsx"); ie.write_xlsx(out,[r]); return "invoice extraction + xlsx export"
    chk("invoice extraction (Excel export)",inv)
    chk("Tesseract engine",tess,fatal=False)
    def rapid():
        if not se.rapidocr_available(): raise RuntimeError("optional - not installed")
        se.get_rapidocr(); return "RapidOCR loaded"
    chk("RapidOCR engine (optional)",rapid,fatal=False)
    lines.append("RESULT: "+("PASS" if state["ok"] else "FAIL"))
    try:
        with open(out_path,"w",encoding="utf-8") as f:f.write("\n".join(lines)+"\n")
    except Exception:pass
    print("\n".join(lines))
    return 0 if state["ok"] else 1


def _log_crash():
    """في وضع EXE بدون نافذة أوامر: اكتب الخطأ في ملف وأظهره للمستخدم."""
    try:
        d=os.path.join(os.environ.get("LOCALAPPDATA",os.path.expanduser("~")),"CanonScanPro"); os.makedirs(d,exist_ok=True)
        log=os.path.join(d,"error.log")
        with open(log,"a",encoding="utf-8") as f:f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{traceback.format_exc()}")
        r=tk.Tk(); r.withdraw()
        messagebox.showerror("Canon Scan Pro","حدث خطأ غير متوقع أثناء التشغيل.\nتفاصيل الخطأ محفوظة في:\n"+log)
        r.destroy()
    except Exception:
        traceback.print_exc()


if __name__=="__main__":
    if "--selftest" in sys.argv:
        i=sys.argv.index("--selftest")
        sys.exit(_selftest(sys.argv[i+1] if len(sys.argv)>i+1 else os.path.join(tempfile.gettempdir(),"canonscan_selftest.txt")))
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CanonScanPro.App")
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
    try: App().mainloop()
    except Exception: _log_crash()
