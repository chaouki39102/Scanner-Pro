# -*- coding: utf-8 -*-
"""
invoice_ui.py — نافذة مراجعة بيانات الفواتير المستخرجة.

المبدأ: البرنامج يقترح والإنسان يعتمد. كل حقل يظهر بلون حالته (سليم / مراجعة / خطأ حسابي / ناقص / محسوب)،
وكل تعديل يدوي يُعاد التحقق منه فورًا (HT + TVA = TTC ...)، ثم يُصدَّر إلى Excel / CSV / JSON أو يُرسل إلى ERP.
"""
import json
import queue
import threading
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import filedialog, messagebox, ttk
from datetime import datetime

from PIL import Image, ImageTk

import invoice_extract as ie
import ui_theme as ui
from ui_theme import T

# (المفتاح، العنوان، النوع) — النوع: text | money | date | pct
SECTIONS = [
    ("بيانات الفاتورة", "doc", [
        ("invoice_number", "رقم الفاتورة", "text"), ("date", "التاريخ", "date"),
        ("supplier", "المورّد", "text"), ("payment_method", "طريقة الدفع", "text"),
        ("due_date", "تاريخ الاستحقاق", "date"), ("currency", "العملة", "text")]),
    ("المعرّفات الجبائية للمورّد", "folder", [
        ("supplier_nif", "NIF", "text"), ("supplier_nis", "NIS", "text"),
        ("supplier_rc", "RC (السجل التجاري)", "text"), ("supplier_ai", "AI (مادة الإخضاع)", "text")]),
    ("الزبون", "image", [
        ("client", "اسم الزبون", "text"), ("client_nif", "NIF الزبون", "text"),
        ("client_rc", "RC الزبون", "text"), ("client_ai", "AI الزبون", "text")]),
    ("المبالغ", "sliders", [
        ("ht", "المجموع خارج الرسم HT", "money"), ("tva", "مبلغ TVA", "money"),
        ("tva_rate", "نسبة TVA %", "pct"), ("timbre", "حقوق الطابع", "money"),
        ("ttc", "المجموع مع الرسم TTC", "money"), ("net", "الصافي للدفع", "money")]),
]
STATUS_NOTE = {"ok": "سليم", "warn": "يحتاج مراجعة", "error": "خطأ حسابي", "missing": "غير موجود", "computed": "محسوب"}


def _fmt(kind, v):
    if v in (None, ""):
        return ""
    if kind == "money":
        try:
            return f"{float(v):,.2f}".replace(",", " ")
        except (TypeError, ValueError):
            return str(v)
    if kind == "pct":
        try:
            return f"{float(v):g}"
        except (TypeError, ValueError):
            return str(v)
    if kind == "date":
        try:
            return datetime.fromisoformat(str(v)).strftime("%d/%m/%Y")
        except ValueError:
            return str(v)
    return str(v)


def _parse(kind, text):
    t = ie.normalize_text(text).strip()
    if not t:
        return None
    if kind in ("money", "pct"):
        try:
            v = ie.parse_amount(t)
        except Exception:
            v = None
        return v
    if kind == "date":
        ds = ie._dates_in(t)
        return ds[0].isoformat() if ds else t
    return t


class StatusField(ui.Field):
    """حقل إدخال يلوّن إطاره بحسب حالة الحقل."""

    def __init__(self, master, variable, justify="right"):
        self.status = "missing"
        super().__init__(master, variable, width=8, justify=justify)

    def set_status(self, st):
        self.status = st
        self.retheme()

    def retheme(self):
        col = {"ok": T["ok"], "warn": T["warn"], "error": T["danger"], "computed": T["accent"]}.get(self.status, T["border"])
        if self._focused:
            col = T["accent"]
        self.configure(bg=T["surface2"], highlightbackground=col, highlightcolor=col, highlightthickness=2 if self.status in ("warn", "error") else 1)
        self.entry.configure(bg=T["surface2"], fg=T["fg"], insertbackground=T["fg"],
                             selectbackground=T["accent"], selectforeground=T["accent_fg"])


class InvoiceDialog(tk.Toplevel):
    def __init__(self, app, results, previews, erp_cfg=None, save_erp_cfg=None, output_dir=None):
        super().__init__(app)
        self.app, self.results, self.previews = app, results, previews
        self.erp_cfg, self.save_erp_cfg = erp_cfg or {}, save_erp_cfg
        self.output_dir = output_dir or ""
        self.idx = 0
        self._loading = False
        self._job = None
        self._photo = None
        self._alive = True
        self._ui_queue = queue.Queue()
        self._ui_job = None
        self.vars, self.fields, self.dots = {}, {}, {}
        self.title("مراجعة بيانات الفواتير — Canon Scan Pro")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1240, sw - 60), min(820, sh - 90)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(980, 600)
        self.configure(bg=T["bg"])
        self.transient(app)
        self._build()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._drain_ui_queue()
        self.after(50, self._load)
        self.grab_set()
        self.focus_set()

    def _on_destroy(self, event):
        if event.widget is self:
            self._alive = False
            if self._ui_job:
                try:self.after_cancel(self._ui_job)
                except Exception:pass
                self._ui_job=None

    def _post_ui(self, callback):
        if self._alive:
            self._ui_queue.put(callback)

    def _drain_ui_queue(self):
        if not self._alive:
            return
        self._ui_job = None
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            self._ui_job = self.after(50, self._drain_ui_queue)
        except Exception:
            pass

    # ------------------------------------------------------------------ بناء
    def _build(self):
        hdr = ui.Themed(self, "surface")
        hdr.pack(fill="x")
        row = ui.Themed(hdr, "surface")
        row.pack(fill="x", padx=20, pady=12)
        ui.label(row, "مراجعة بيانات الفاتورة", "h2", "fg", "surface").pack(side="right")
        self.score_lbl = ui.label(row, "", "small_b", "fg2", "surface", anchor="e")
        self.score_lbl.pack(side="right", padx=14)
        self.btn_next = ui.IconBtn(row, "next", lambda: self.go(1), tip="الفاتورة التالية", bg_token="surface", variant="secondary")
        self.btn_next.pack(side="left")
        self.nav_lbl = ui.label(row, "", "body_b", "fg", "surface", anchor="center", width=16)
        self.nav_lbl.pack(side="left", padx=6)
        self.btn_prev = ui.IconBtn(row, "prev", lambda: self.go(-1), tip="الفاتورة السابقة", bg_token="surface", variant="secondary")
        self.btn_prev.pack(side="left")
        ui.HSep(hdr).pack(fill="x")

        foot = ui.Themed(self, "surface")
        foot.pack(side="bottom", fill="x")
        ui.HSep(foot).pack(fill="x")
        fr = ui.Themed(foot, "surface")
        fr.pack(fill="x", padx=20, pady=12)
        n = len(self.results)
        suffix = f" ({n} فواتير)" if n > 1 else ""
        ui.Btn(fr, "تصدير Excel" + suffix, self.export_xlsx, icon="export", variant="primary", height=40, fill_x=False).pack(side="right")
        ui.Btn(fr, "CSV", self.export_csv, height=40, fill_x=False, icon="text").pack(side="right", padx=(8, 0))
        ui.Btn(fr, "JSON", self.export_json, height=40, fill_x=False, icon="doc").pack(side="right", padx=(8, 0))
        ui.Btn(fr, "نسخ السطر", self.copy_row, height=40, fill_x=False, icon="layers", tip="ينسخ بيانات الفاتورة لصقًا مباشرًا في Excel").pack(side="right", padx=(8, 0))
        ui.Btn(fr, "إرسال إلى ERP", self.send_erp, height=40, fill_x=False, icon="scan", variant="soft", tip="يرسل الفواتير كـ JSON إلى واجهة API لنظامك").pack(side="right", padx=(8, 0))
        ui.Btn(fr, "إغلاق", self.destroy, height=40, fill_x=False).pack(side="left")
        ui.Btn(fr, "عرض النص المقروء", self.show_text, height=40, fill_x=False, icon="eye").pack(side="left", padx=(0, 8))

        body = ui.Themed(self, "bg")
        body.pack(fill="both", expand=True, padx=16, pady=12)
        # يمين: الحقول (قابلة للتمرير)
        right = ui.Themed(body, "bg", width=560)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        sb = ttk.Scrollbar(right, orient="vertical", style="Slim.Vertical.TScrollbar")
        self.cv = tk.Canvas(right, highlightthickness=0, bd=0, bg=T["bg"], yscrollcommand=lambda a, b: self._sb_set(sb, a, b))
        sb.configure(command=self.cv.yview)
        self._sb = sb
        self.cv.pack(side="right", fill="both", expand=True)
        host = ui.Themed(self.cv, "bg")
        win = self.cv.create_window((0, 0), window=host, anchor="nw")
        host.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(win, width=e.width))
        self._host = host
        for title, icon, flds in SECTIONS:
            c = ui.Card(host, title, icon)
            c.pack(fill="x", padx=(0, 8), pady=(0, 10))
            grid = ui.Themed(c.body, "surface")
            grid.pack(fill="x")
            grid.columnconfigure((0, 1), weight=1, uniform="f")
            for i, (key, ttl, kind) in enumerate(flds):
                cell = ui.Themed(grid, "surface")
                cell.grid(row=i // 2, column=1 - (i % 2), sticky="ew", padx=4, pady=4)
                top = ui.Themed(cell, "surface")
                top.pack(fill="x")
                dot = tk.Canvas(top, width=10, height=10, highlightthickness=0, bd=0, bg=T["surface"])
                dot.pack(side="right", padx=(0, 5), pady=3)
                ui.label(top, ttl, "small", "fg2", "surface", anchor="e").pack(side="right")
                var = tk.StringVar()
                just = "left" if kind in ("money", "pct") or key.endswith(("_nif", "_nis", "_rc", "_ai", "number")) else "right"
                fld = StatusField(cell, var, justify=just)
                fld.pack(fill="x", pady=(3, 0))
                var.trace_add("write", lambda *a, k=key, kd=kind: self._edited(k, kd))
                self.vars[key], self.fields[key], self.dots[key] = var, fld, dot
        # يسار: معاينة + تنبيهات + بنود
        left = ui.Themed(body, "bg")
        left.pack(side="right", fill="both", expand=True, padx=(0, 4))
        self.prev = tk.Canvas(left, bg=T["canvas"], highlightthickness=1, highlightbackground=T["border"], bd=0, height=260)
        self.prev.pack(fill="both", expand=True)
        self.prev.bind("<Configure>", lambda e: self._draw_preview())
        self.warn_card = ui.Card(left, "تنبيهات وفحوص", "check")
        self.warn_card.pack(fill="x", pady=(10, 0))
        self.warn_box = ui.Themed(self.warn_card.body, "surface")
        self.warn_box.pack(fill="x")
        self.items_card = ui.Card(left, "البنود", "layers")
        self.items_card.pack(fill="x", pady=(10, 0))
        self._style_tree()
        cols = ("total", "unit", "qty", "desc")
        self.tree = ttk.Treeview(self.items_card.body, columns=cols, show="headings", height=5, style="Inv.Treeview")
        for c, t, w, a in (("total", "المجموع", 100, "e"), ("unit", "سعر الوحدة", 100, "e"), ("qty", "الكمية", 60, "e"), ("desc", "الوصف", 260, "e")):
            self.tree.heading(c, text=t, anchor="e")
            self.tree.column(c, width=w, anchor=a, stretch=(c == "desc"))
        self.tree.pack(fill="x")
        self.tree.bind("<Delete>", self._del_item)
        ui.label(self.items_card.body, "حدّد بندًا واضغط Delete لحذفه إن قُرئ خطأً.", "small", "muted").pack(fill="x", pady=(4, 0))

    def _style_tree(self):
        st = ttk.Style(self)
        st.configure("Inv.Treeview", background=T["surface2"], fieldbackground=T["surface2"], foreground=T["fg"],
                     rowheight=26, borderwidth=0, font=ui.F("small"))
        st.configure("Inv.Treeview.Heading", background=T["sunken"], foreground=T["fg2"], font=ui.F("small_b"), relief="flat")
        st.map("Inv.Treeview", background=[("selected", T["accent_soft"])], foreground=[("selected", T["accent_soft_fg"])])

    def _sb_set(self, sb, a, b):
        sb.set(a, b)
        if float(a) <= 0.0 and float(b) >= 1.0:
            sb.pack_forget()
        elif not sb.winfo_ismapped():
            sb.pack(side="left", fill="y", before=self.cv)

    def _wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            if w is self.cv or w is self._host:
                self.cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
                return
            w = getattr(w, "master", None)

    # --------------------------------------------------------------- عرض فاتورة
    @property
    def cur(self):
        return self.results[self.idx]

    def go(self, d):
        self.idx = max(0, min(len(self.results) - 1, self.idx + d))
        self._load()

    def _load(self):
        self._loading = True
        r = self.cur
        for title, _, flds in SECTIONS:
            for key, _, kind in flds:
                self.vars[key].set(_fmt(kind, r["fields"].get(key)))
        self._loading = False
        self._refresh()
        self._draw_preview()
        self._fill_items()
        n = len(self.results)
        self.nav_lbl.configure(text=f"{self.idx + 1} / {n}  —  {r.get('source', '')}")
        self.btn_prev.set_state("normal" if self.idx > 0 else "disabled")
        self.btn_next.set_state("normal" if self.idx < n - 1 else "disabled")
        self.cv.yview_moveto(0)

    def _edited(self, key, kind):
        if self._loading:
            return
        v = _parse(kind, self.vars[key].get())
        self.cur["fields"][key] = v
        self.cur["meta"].setdefault(key, {})["status"] = "ok" if v not in (None, "") else "missing"
        self.cur["meta"][key]["note"] = "عُدِّل يدويًا"
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(350, self._revalidate)

    def _revalidate(self):
        self._job = None
        r = self.cur
        r["warnings"] = ie.validate(r)
        r["score"] = ie.score(r)
        self._refresh()

    def _refresh(self):
        r = self.cur
        for key, fld in self.fields.items():
            st = ie.field_status(r, key)
            fld.set_status(st)
            d = self.dots[key]
            d.delete("all")
            d.configure(bg=T["surface"])
            col = {"ok": T["ok"], "warn": T["warn"], "error": T["danger"], "computed": T["accent"]}.get(st, T["border_strong"])
            d.create_oval(1, 1, 9, 9, fill=col, outline="")
        sc = r.get("score", 0)
        self.score_lbl.configure(text=f"الثقة التقديرية: {sc}%", fg=T["ok"] if sc >= 85 else T["warn"] if sc >= 60 else T["danger"])
        for w in self.warn_box.winfo_children():
            w.destroy()
        shown = [(l, m) for l, _, m in r["warnings"] if l in ("error", "warn", "info")]
        if not shown:
            ui.label(self.warn_box, "✓ لا توجد تنبيهات — الفحوص الحسابية سليمة.", "small_b", "ok").pack(fill="x")
        for lvl, msg in shown[:8]:
            sym, tok = {"error": ("✖ ", "danger"), "warn": ("⚠ ", "warn"), "info": ("ℹ ", "muted")}[lvl]
            ui.label(self.warn_box, sym + msg, "small", tok, wraplength=470).pack(fill="x", pady=1)

    def _fill_items(self):
        self.tree.delete(*self.tree.get_children())
        for i, it in enumerate(self.cur["items"]):
            self.tree.insert("", "end", iid=str(i), values=(f"{it['total']:,.2f}".replace(",", " "),
                                                         f"{it['unit_price']:,.2f}".replace(",", " "),
                                                         f"{it['qty']:g}", it["desc"] + ("" if it["ok"] else "  ⚠")))

    def _del_item(self, _e=None):
        sel = sorted((int(i) for i in self.tree.selection()), reverse=True)
        for i in sel:
            del self.cur["items"][i]
        self.cur["fields"]["items_count"] = len(self.cur["items"])
        self._fill_items()
        self._revalidate()

    def _draw_preview(self):
        c = self.prev
        c.delete("all")
        c.configure(bg=T["canvas"])
        im = self.previews[self.idx] if self.idx < len(self.previews) else None
        if im is None or c.winfo_width() < 40:
            return
        w, h = c.winfo_width() - 20, c.winfo_height() - 20
        s = min(w / im.width, h / im.height, 1.6)
        pic = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(pic)
        c.create_image(c.winfo_width() // 2, c.winfo_height() // 2, image=self._photo)

    # -------------------------------------------------------------- إجراءات
    def _stamp(self):
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _ask(self, ext, kinds):
        return filedialog.asksaveasfilename(parent=self, initialdir=self.output_dir or None, defaultextension=ext,
                                            initialfile=f"Invoices_{self._stamp()}{ext}", filetypes=kinds)

    def export_xlsx(self):
        p = self._ask(".xlsx", [("Excel", "*.xlsx")])
        if not p:
            return
        try:
            ie.write_xlsx(p, self.results)
        except ImportError:
            return messagebox.showerror("مكتبة ناقصة", "تصدير Excel يحتاج المكتبة openpyxl.\nثبّتها بالأمر: pip install openpyxl\nأو استخدم CSV.", parent=self)
        except Exception as e:
            return messagebox.showerror("فشل التصدير", str(e), parent=self)
        messagebox.showinfo("تم", f"حُفظ الملف:\n{p}", parent=self)

    def export_csv(self):
        p = self._ask(".csv", [("CSV", "*.csv")])
        if p:
            try:
                ie.write_csv(p, self.results)
                messagebox.showinfo("تم", f"حُفظ الملف:\n{p}", parent=self)
            except Exception as e:
                messagebox.showerror("فشل التصدير", str(e), parent=self)

    def export_json(self):
        p = self._ask(".json", [("JSON", "*.json")])
        if p:
            try:
                ie.write_json(p, self.results)
                messagebox.showinfo("تم", f"حُفظ الملف:\n{p}", parent=self)
            except Exception as e:
                messagebox.showerror("فشل التصدير", str(e), parent=self)

    def copy_row(self):
        row = ie.to_row(self.cur)
        self.clipboard_clear()
        self.clipboard_append("\t".join("" if row[k] is None else str(ie._safe_cell(row[k])) for k, _ in ie.COLUMNS))
        messagebox.showinfo("نُسخ", "نُسخ سطر الفاتورة. الصقه في Excel بـ Ctrl+V.", parent=self)

    def show_text(self):
        win = tk.Toplevel(self)
        win.title("النص المقروء (OCR)")
        win.geometry("720x560")
        win.configure(bg=T["bg"])
        t = tk.Text(win, wrap="word", font=ui.F("body"), bg=T["surface"], fg=T["fg"], bd=0, padx=12, pady=10)
        t.pack(fill="both", expand=True)
        t.insert("1.0", self.cur.get("text") or "(لا يوجد نص محفوظ)")
        t.configure(state="disabled")

    # ------------------------------------------------------------------- ERP
    def send_erp(self):
        cfg = self._erp_dialog()
        if not cfg:
            return
        url, token = cfg
        payloads = [ie.erp_payload(r, r.get("source", "")) for r in self.results]

        def worker():
            ok, errs = 0, []
            for p in payloads:
                try:
                    req = urllib.request.Request(url, data=json.dumps(p, ensure_ascii=False).encode("utf-8"), method="POST")
                    req.add_header("Content-Type", "application/json; charset=utf-8")
                    req.add_header("Accept", "application/json")
                    if token:
                        req.add_header("Authorization", "Bearer " + token)
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        if 200 <= resp.status < 300:
                            ok += 1
                        else:
                            errs.append(f"HTTP {resp.status}")
                except urllib.error.HTTPError as e:
                    errs.append(f"HTTP {e.code}: {e.reason}")
                except Exception as e:
                    errs.append(str(e))
            msg = f"أُرسلت {ok} من {len(payloads)} فاتورة."
            if errs:
                msg += "\nأول خطأ: " + errs[0]
            def report():
                try:
                    if self.app.winfo_exists():
                        self.app.status.set("ERP: " + msg.replace("\n", " | "))
                except Exception:
                    pass
                if self._alive:
                    try:messagebox.showinfo("إرسال إلى ERP", msg, parent=self)
                    except Exception:pass
            post=getattr(self.app,"_post_ui",None)
            if callable(post):post(report)
            else:self._post_ui(report)

        threading.Thread(target=worker, daemon=True).start()

    def _erp_dialog(self):
        win = tk.Toplevel(self)
        win.title("إعداد الإرسال إلى ERP")
        win.configure(bg=T["surface"])
        win.transient(self)
        win.geometry("520x330")
        win.grab_set()
        uv, tv = tk.StringVar(value=self.erp_cfg.get("url", "")), tk.StringVar(value=self.erp_cfg.get("token", ""))
        pad = ui.Themed(win, "surface")
        pad.pack(fill="both", expand=True, padx=20, pady=16)
        ui.label(pad, "عنوان واجهة API (POST بصيغة JSON)", "small", "fg2").pack(fill="x")
        ui.Field(pad, uv, justify="left").pack(fill="x", pady=(4, 10))
        ui.label(pad, "رمز الوصول Bearer (اختياري)", "small", "fg2").pack(fill="x")
        ui.Field(pad, tv, justify="left").pack(fill="x", pady=(4, 10))
        ui.label(pad, "يُرسل طلب POST لكل فاتورة بحقول إنجليزية ثابتة (invoice / items / needs_review / confidence). "
                      "الرمز يُحفظ محليًا في ملف إعدادات البرنامج دون تشفير.", "small", "muted", wraplength=460).pack(fill="x")
        out = {}

        def ok():
            u = uv.get().strip()
            if not u.lower().startswith(("http://", "https://")):
                return messagebox.showerror("عنوان غير صحيح", "ابدأ العنوان بـ http:// أو https://", parent=win)
            out["v"] = (u, tv.get().strip())
            if self.save_erp_cfg:
                self.save_erp_cfg(u, tv.get().strip())
            win.destroy()
        row = ui.Themed(pad, "surface")
        row.pack(fill="x", side="bottom")
        ui.Btn(row, "إرسال", ok, variant="primary", height=40, fill_x=False).pack(side="right")
        ui.Btn(row, "إلغاء", win.destroy, height=40, fill_x=False).pack(side="right", padx=8)
        self.wait_window(win)
        return out.get("v")
