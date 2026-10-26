# Canon Scan Pro

تطبيق سطح مكتب لمسح المستندات وتحسينها واستخراج بيانات الفواتير — يعمل محليًا على Windows.
A Windows desktop application for scanning, enhancing, and extracting invoice data — fully local.
Application de bureau Windows pour numériser, améliorer et extraire des factures — 100 % local.

**Version / الإصدار / Version :** 7.3.0

[العربية](#العربية) · [English](#english) · [Français](#français)

---

## العربية

### نظرة عامة
**Canon Scan Pro** تطبيق مكتبي بواجهة عربية مبني بـ Python وTkinter، مخصص للمسح من ماسحة **Canon MF3010** عبر **Windows WIA**، ثم معالجة الصفحات تلقائيًا وتصديرها كصور أو PDF. يستطيع أيضًا قراءة الفواتير بصيغة OCR واستخراج حقولها والتحقق منها حسابيًا وتصديرها إلى Excel أو CSV أو JSON.

كل المعالجة تتم على جهازك. لا تُرفع الصور إلى الإنترنت، باستثناء ما تختاره صراحةً عند تفعيل إرسال الفواتير إلى واجهة ERP.

### الميزات الرئيسية
- مسح صفحة A4 كاملة من Canon MF3010 عبر WIA، مع دعم المسح بالألوان أو الرمادي أو الأبيض والأسود.
- معالجة تلقائية: قص الحدود، إزالة حواف الماسح السوداء، تصحيح الميلان، إزالة الظلال، توحيد الخلفية وتقليل التشويش.
- فلاتر جاهزة: ألوان محسّنة، أبيض وأسود واضح، رمادي نظيف، وأصل بدون تعديل.
- تحرير يدوي: تدوير، قص بالماوس، سطوع، تباين، تشبّع، جاما، حدة، وتراجع/إعادة حتى 20 خطوة.
- مقارنة قبل/بعد، تكبير بعجلة الفأرة، شريط مصغّرات، وإعادة ترتيب الصفحات بالسحب.
- حفظ الصور بصيغ PNG وJPEG وWEBP وTIFF وBMP، وتصدير PDF متعدد الصفحات أو TIFF متعدد الصفحات.
- OCR اختياري إلى TXT أو PDF قابل للبحث، بمحرك **Tesseract** (يدعم العربية والفرنسية والإنجليزية) أو **RapidOCR** (محلي وسريع للغات اللاتينية).
- استخراج بيانات الفواتير: رقم الفاتورة، التاريخ، المورّد والزبون، NIF/NIS/RC/AI، HT وTVA وTTC والطابع، وجدول البنود، مع فحوص حسابية ونافذة مراجعة.
- تصدير الفواتير إلى Excel أو CSV أو JSON، وإرسال اختياري إلى ERP عبر JSON.
- أدوات PDF محلية: دمج، تقسيم، استخراج صفحات، حذف صفحات، تدوير، ضغط، وحماية بكلمة مرور (AES-256).
- حذف الصفحات الفارغة تلقائيًا، وتسمية ذكية للملفات بالتاريخ واختياريًا بأول سطر من النص.
- حفظ جلسة العمل تلقائيًا واستعادتها، وثيمان فاتح وداكن.

### المتطلبات
- **Windows 10 أو 11** (المسح عبر WIA يحتاج Windows وتعريف الماسحة).
- **Python 3.10 – 3.13** عند التشغيل أو البناء من المصدر.
- ماسحة **Canon MF3010** مع تعريف WIA من موقع Canon (غير مضمّن في المستودع).
- **Tesseract OCR** (اختياري لكنه مطلوب للعربية والفرنسية) مع ملفات `ara.traineddata` و`fra.traineddata` و`eng.traineddata`.

### التشغيل من المصدر
```bat
py -m pip install -r requirements.txt
run.bat
```
أو مباشرة:
```bat
py canon_scan_pro.py
```
> يجب أن يبقى `canon_scan_pro.py` بجانب `scan_enhance.py` و`ui_theme.py` و`invoice_extract.py` و`invoice_ui.py` و`extras.py` و`app_icon.py`.

### البناء وتوليد المثبّت
```bat
build_release.bat
```
يقوم السكربت بـ:
1. إنشاء بيئة `.venv-build` وتثبيت المتطلبات.
2. بناء EXE بـ PyInstaller (وضع onedir).
3. تشغيل فحص ذاتي (`--selftest`) للتأكد من وجود المكتبات والنماذج.
4. إنشاء المثبّت عبر Inno Setup.

المخرجات في مجلد `release`:
- `CanonScanPro_Setup_7.3.0.exe` — مثبّت كامل يعمل دون إنترنت أو Python.
- `CanonScanPro_Portable.zip` — نسخة محمولة.
- ملف `SHA-256` للتحقق من السلامة.

التثبيت الصامت:
```bat
CanonScanPro_Setup_7.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### OCR واستخراج الفواتير
- اختر محرك التعرّف من تبويب التصدير: **تلقائي** أو **Tesseract** أو **RapidOCR**.
- الوضع التلقائي يستخدم Tesseract إن كان جاهزًا، وإلا ينتقل إلى RapidOCR.
- استخراج الفواتير متاح من تبويب التصدير أو بالاختصار `Ctrl+I`، ويدعم العربية والفرنسية والإنجليزية.
- كل حقل في نافذة المراجعة يحمل مؤشرًا لونه: أخضر سليم، برتقالي يحتاج مراجعة، أحمر خطأ حسابي، أزرق محسوب.
- للقياس على فواتيرك، استخدم السكربت المحلي:
```bat
python tools\benchmark_invoices.py --dir invoices --init-truth
python tools\benchmark_invoices.py --dir invoices --truth invoices\truth.csv
```

### الاختبارات
```bat
python -m unittest tests.test_benchmark -v
```

### بنية المشروع
| الملف | الوظيفة |
|---|---|
| `canon_scan_pro.py` | نقطة الدخول والواجهة الرئيسية ومنطق المسح والتصدير |
| `scan_enhance.py` | خوارزميات تحسين الصور وتصحيح الميلان والقص |
| `invoice_extract.py` | قراءة الفواتير واستخراج الحقول والتحقق الحسابي |
| `invoice_ui.py` | نافذة مراجعة الفاتورة |
| `ui_theme.py` | الثيمات والعناصر الرسومية |
| `extras.py` | أدوات PDF وحذف الصفحات الفارغة والتسمية الذكية |
| `app_icon.py` | توليد أيقونة التطبيق |
| `build_release.ps1` / `.bat` | بناء EXE والمثبّت والنسخة المحمولة |
| `installer.iss` | إعدادات مثبّت Inno Setup |
| `tests/` | اختبارات آلية |
| `tools/benchmark_invoices.py` | قياس دقة استخراج الفواتير |

### ملاحظات مهمة
- هذا مستودع مصدر؛ يجب بناء EXE على Windows لأن PyInstaller لا ينتج تطبيق Windows من Linux.
- Tesseract وبيانات لغاته برنامج خارجي منفصل، ولا يتضمنه EXE المصدر تلقائيًا.
- لا تعتمد على OCR وحده في الأرقام والمبالغ؛ راجع الفواتير يدويًا واحتفظ دائمًا بالنسخة الأصلية.
- الملف غير موقّع رقميًا، لذا قد يظهر تحذير SmartScreen عند أول تشغيل.
- السجل التفصيلي الكامل للتغييرات متوفر بالعربية في [`README_AR.md`](README_AR.md).
- لا يوجد ملف ترخيص في المستودع حاليًا؛ تواصل مع المؤلف قبل إعادة التوزيع.

---

## English

### Overview
**Canon Scan Pro** is a Python/Tkinter desktop application for scanning with a **Canon MF3010** through **Windows WIA**, enhancing the scanned pages, and exporting them as images, PDF, or searchable PDF. It also performs OCR-based invoice extraction with arithmetic validation and exports the results to Excel, CSV, or JSON.

Everything runs locally on your machine. No image is uploaded to the internet unless you explicitly enable invoice submission to an ERP endpoint.

### Key features
- Full A4 scanning through WIA, in color, grayscale, or black and white.
- Automatic processing: border cropping, scanner-edge removal, deskew, shadow removal, background normalization, and noise reduction.
- Presets: enhanced color, crisp black and white, clean grayscale, and original.
- Manual editing: rotate, mouse crop, brightness, contrast, saturation, gamma, sharpness, plus undo/redo (up to 20 steps).
- Before/after comparison, mouse-wheel zoom, thumbnail strip, and drag-to-reorder pages.
- Save as PNG, JPEG, WEBP, TIFF, or BMP; export multi-page PDF or multi-page TIFF.
- Optional OCR to TXT or searchable PDF using **Tesseract** (Arabic, French, English) or **RapidOCR** (fast local recognition for Latin scripts).
- Invoice extraction: invoice number, dates, supplier, client, NIF/NIS/RC/AI, HT/TVA/TTC/timbre, line items, with automatic arithmetic checks and a review dialog.
- Export invoices to Excel, CSV, or JSON; optionally POST to an ERP as JSON.
- Local PDF tools: merge, split, extract, delete, rotate, compress, and AES-256 password protection.
- Automatic blank-page removal and smart file naming with date/time and an optional OCR snippet.
- Automatic session save/restore, and light/dark themes.

### Requirements
- **Windows 10 or 11** (WIA scanning requires Windows and the scanner driver).
- **Python 3.10 – 3.13** to run or build from source.
- **Canon MF3010** with the WIA driver installed from Canon (not bundled).
- **Tesseract OCR** (optional, but required for Arabic and French) with `ara.traineddata`, `fra.traineddata`, and `eng.traineddata`.

### Run from source
```bat
py -m pip install -r requirements.txt
run.bat
```
Or directly:
```bat
py canon_scan_pro.py
```
> Keep `canon_scan_pro.py` next to `scan_enhance.py`, `ui_theme.py`, `invoice_extract.py`, `invoice_ui.py`, `extras.py`, and `app_icon.py`.

### Build and package
```bat
build_release.bat
```
The script will:
1. Create `.venv-build` and install dependencies.
2. Build the EXE with PyInstaller (onedir).
3. Run a self-test (`--selftest`) to verify libraries and models.
4. Build the installer with Inno Setup.

Outputs in `release`:
- `CanonScanPro_Setup_7.3.0.exe` — full offline installer, no Python required.
- `CanonScanPro_Portable.zip` — portable build.
- A SHA-256 checksum file.

Silent install:
```bat
CanonScanPro_Setup_7.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### OCR and invoice extraction
- Choose the recognition engine in the Export tab: **Auto**, **Tesseract**, or **RapidOCR**.
- Auto uses Tesseract when available and falls back to RapidOCR.
- Invoice extraction is available from the Export tab or with `Ctrl+I`, and supports Arabic, French, and English.
- In the review dialog, each field is color-coded: green (valid), orange (needs review), red (arithmetic error), blue (computed).
- To benchmark on your own invoices, use the local script:
```bat
python tools\benchmark_invoices.py --dir invoices --init-truth
python tools\benchmark_invoices.py --dir invoices --truth invoices\truth.csv
```

### Tests
```bat
python -m unittest tests.test_benchmark -v
```

### Project structure
| File | Purpose |
|---|---|
| `canon_scan_pro.py` | Entry point, main UI, scanning and export logic |
| `scan_enhance.py` | Image enhancement, deskew, and cropping algorithms |
| `invoice_extract.py` | Invoice OCR parsing, field extraction, and validation |
| `invoice_ui.py` | Invoice review dialog |
| `ui_theme.py` | Themes and reusable UI widgets |
| `extras.py` | PDF tools, blank-page removal, and smart naming |
| `app_icon.py` | Application icon generation |
| `build_release.ps1` / `.bat` | EXE, installer, and portable build |
| `installer.iss` | Inno Setup configuration |
| `tests/` | Automated tests |
| `tools/benchmark_invoices.py` | Invoice extraction accuracy benchmark |

### Important notes
- This is a source repository; build the EXE on Windows, since PyInstaller cannot produce a Windows app from Linux.
- Tesseract and its language data are separate third-party software and are not embedded by the source build.
- Do not rely on OCR alone for amounts or identifiers. Always keep the original scan and review invoices manually.
- The executable is not code-signed, so Windows SmartScreen may warn on first launch.
- The full, detailed changelog is available in Arabic in [`README_AR.md`](README_AR.md).
- No license file is currently included in this repository; contact the author before redistributing.

---

## Français

### Aperçu
**Canon Scan Pro** est une application de bureau Python/Tkinter destinée à la numérisation avec un **Canon MF3010** via **Windows WIA**, à l'amélioration des pages numérisées et à leur export en images, PDF ou PDF recherchable. Elle effectue également de l'OCR sur les factures, extrait les champs, vérifie les calculs et exporte les résultats vers Excel, CSV ou JSON.

Tout le traitement se fait localement. Aucune image n'est envoyée sur Internet, sauf si vous activez explicitement l'envoi des factures vers un point de terminaison ERP.

### Fonctionnalités principales
- Numérisation A4 complète via WIA, en couleur, niveaux de gris ou noir et blanc.
- Traitement automatique : recadrage des bords, suppression des bords du scanner, redressement, suppression des ombres, normalisation du fond et réduction du bruit.
- Préréglages : couleur améliorée, noir et blanc net, niveaux de gris propres, et original.
- Édition manuelle : rotation, recadrage à la souris, luminosité, contraste, saturation, gamma, netteté, avec annuler/rétablir (jusqu'à 20 étapes).
- Comparaison avant/après, zoom à la molette, bande de vignettes et réorganisation des pages par glisser-déposer.
- Enregistrement en PNG, JPEG, WEBP, TIFF ou BMP ; export PDF multipage ou TIFF multipage.
- OCR facultatif vers TXT ou PDF recherchable avec **Tesseract** (arabe, français, anglais) ou **RapidOCR** (reconnaissance locale rapide pour les alphabets latins).
- Extraction de factures : numéro, dates, fournisseur, client, NIF/NIS/RC/AI, HT/TVA/TTC/timbre, lignes d'articles, avec contrôles arithmétiques automatiques et fenêtre de révision.
- Export des factures vers Excel, CSV ou JSON ; envoi facultatif vers un ERP en JSON.
- Outils PDF locaux : fusion, division, extraction, suppression, rotation, compression et protection par mot de passe AES-256.
- Suppression automatique des pages blanches et nommage intelligent avec date/heure et extrait OCR facultatif.
- Sauvegarde/restauration automatique de la session, et thèmes clair/sombre.

### Prérequis
- **Windows 10 ou 11** (la numérisation WIA nécessite Windows et le pilote du scanner).
- **Python 3.10 – 3.13** pour exécuter ou compiler depuis les sources.
- **Canon MF3010** avec le pilote WIA installé depuis le site Canon (non fourni).
- **Tesseract OCR** (facultatif, mais nécessaire pour l'arabe et le français) avec `ara.traineddata`, `fra.traineddata` et `eng.traineddata`.

### Exécution depuis les sources
```bat
py -m pip install -r requirements.txt
run.bat
```
Ou directement :
```bat
py canon_scan_pro.py
```
> `canon_scan_pro.py` doit rester à côté de `scan_enhance.py`, `ui_theme.py`, `invoice_extract.py`, `invoice_ui.py`, `extras.py` et `app_icon.py`.

### Compilation et packaging
```bat
build_release.bat
```
Le script :
1. Crée `.venv-build` et installe les dépendances.
2. Compile l'EXE avec PyInstaller (mode onedir).
3. Lance un autotest (`--selftest`) pour vérifier les bibliothèques et les modèles.
4. Génère l'installateur avec Inno Setup.

Résultats dans `release` :
- `CanonScanPro_Setup_7.3.0.exe` — installateur complet, hors ligne, sans Python.
- `CanonScanPro_Portable.zip` — version portable.
- Un fichier de contrôle SHA-256.

Installation silencieuse :
```bat
CanonScanPro_Setup_7.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### OCR et extraction de factures
- Choisissez le moteur dans l'onglet Export : **Auto**, **Tesseract** ou **RapidOCR**.
- Le mode Auto utilise Tesseract s'il est disponible, sinon RapidOCR.
- L'extraction de factures est accessible depuis l'onglet Export ou avec `Ctrl+I`, et prend en charge l'arabe, le français et l'anglais.
- Dans la fenêtre de révision, chaque champ est coloré : vert (valide), orange (à vérifier), rouge (erreur de calcul), bleu (calculé).
- Pour évaluer la précision sur vos propres factures, utilisez le script local :
```bat
python tools\benchmark_invoices.py --dir invoices --init-truth
python tools\benchmark_invoices.py --dir invoices --truth invoices\truth.csv
```

### Tests
```bat
python -m unittest tests.test_benchmark -v
```

### Structure du projet
| Fichier | Rôle |
|---|---|
| `canon_scan_pro.py` | Point d'entrée, interface principale, numérisation et export |
| `scan_enhance.py` | Algorithmes d'amélioration, de redressement et de recadrage |
| `invoice_extract.py` | Analyse OCR, extraction et validation des factures |
| `invoice_ui.py` | Fenêtre de révision des factures |
| `ui_theme.py` | Thèmes et composants d'interface réutilisables |
| `extras.py` | Outils PDF, suppression des pages blanches et nommage intelligent |
| `app_icon.py` | Génération de l'icône de l'application |
| `build_release.ps1` / `.bat` | Compilation de l'EXE, de l'installateur et de la version portable |
| `installer.iss` | Configuration Inno Setup |
| `tests/` | Tests automatisés |
| `tools/benchmark_invoices.py` | Évaluation de la précision d'extraction des factures |

### Remarques importantes
- Ce dépôt contient les sources ; compilez l'EXE sous Windows, car PyInstaller ne peut pas produire une application Windows depuis Linux.
- Tesseract et ses données linguistiques sont des logiciels tiers séparés et ne sont pas intégrés par la compilation depuis les sources.
- Ne vous fiez pas uniquement à l'OCR pour les montants ou les identifiants. Conservez toujours le scan original et vérifiez les factures manuellement.
- L'exécutable n'est pas signé numériquement ; Windows SmartScreen peut afficher un avertissement au premier lancement.
- Le journal détaillé des modifications est disponible en arabe dans [`README_AR.md`](README_AR.md).
- Aucun fichier de licence n'est actuellement inclus dans ce dépôt ; contactez l'auteur avant toute redistribution.
