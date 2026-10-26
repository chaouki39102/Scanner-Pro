# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — يُنتج مجلد dist\CanonScanPro (onedir): أسرع تشغيلًا وأقل إنذارات من مضادات الفيروسات من onefile.
from PyInstaller.utils.hooks import collect_all

datas = [
    ('assets/CanonScanPro.ico', 'assets'),
    ('assets/CanonScanPro.png', 'assets'),
    ('assets/models/ESPCN_x2.pb', 'assets/models'),   # نموذج رفع الدقة مضمّن: يعمل بدون إنترنت
]
binaries = []
hiddenimports = ['win32com.client', 'pythoncom', 'pywintypes', 'win32timezone',
                 'scan_enhance', 'ui_theme', 'app_icon', 'extras', 'invoice_extract', 'invoice_ui', 'openpyxl', 'pytesseract', 'winreg',
                 'rapidocr', 'rapidocr_onnxruntime', 'onnxruntime', 'pyclipper', 'shapely']

for pkg in ('cv2', 'PIL', 'skimage', 'scipy', 'pywt', 'pymupdf', 'fitz',
            'rapidocr', 'rapidocr_onnxruntime', 'onnxruntime', 'pyclipper', 'shapely', 'openpyxl'):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as exc:       # حزمة اختيارية (مثل fitz في الإصدارات الحديثة)
        print('collect_all skipped for', pkg, exc)

a = Analysis(
    ['canon_scan_pro.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'IPython', 'pytest', 'sphinx', 'notebook',
              'torch', 'tensorflow', 'pandas', 'sklearn', 'imageio_ffmpeg', 'numba'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CanonScanPro',
    icon='assets/CanonScanPro.ico',
    version='version_info.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='CanonScanPro',
)
