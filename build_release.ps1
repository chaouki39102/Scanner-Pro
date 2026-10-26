# Canon Scan Pro - one-click release builder (Windows 10/11, PowerShell 5.1+)
# Output: release\CanonScanPro_Setup_<version>.exe  (installs app + OCR engine + Arabic/French data silently)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$log = Join-Path $PSScriptRoot 'build_log.txt'
Start-Transcript -Path $log -Force | Out-Null
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "ERROR: $m" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }
function Run([string]$exe, [string[]]$a) { & $exe @a; if ($LASTEXITCODE -ne 0) { Fail "$exe failed (exit $LASTEXITCODE). See build_log.txt" } }
function Download($url, $dest) {
    Write-Host "    downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -UserAgent 'Mozilla/5.0'
}

try {
    # ---------------------------------------------------------------- 1) Python
    Step '1/7 Checking Python (3.10 - 3.13)'
    function Find-Python {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            try { $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null); if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
        }
        return $null
    }
    $py = Find-Python
    if (-not $py) {
        Write-Host '    Python not found - installing Python 3.12 silently via winget...'
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
            $py = Find-Python
        }
    }
    if (-not $py) { Fail 'Python 3.10-3.13 is required. Install it from python.org (tick "Add python.exe to PATH") and run this script again.' }
    Write-Host "    using $py"

    # ------------------------------------------------- 2) venv + requirements (silent)
    Step '2/7 Installing build requirements silently into .venv-build'
    if (-not (Test-Path '.venv-build\Scripts\python.exe')) { Run $py @('-m', 'venv', '.venv-build') }
    $vpy = (Resolve-Path '.venv-build\Scripts\python.exe').Path
    Run $vpy @('-m', 'pip', 'install', '-q', '--disable-pip-version-check', '--upgrade', 'pip')
    Run $vpy @('-m', 'pip', 'install', '-q', '--disable-pip-version-check', '-r', 'requirements.txt')
    # RapidOCR's dependency (opencv-python) can overwrite opencv-contrib-python.
    # Reinstall contrib last so dnn_superres (AI upscaling) keeps working.
    Run $vpy @('-m', 'pip', 'install', '-q', '--disable-pip-version-check', '--force-reinstall', '--no-deps', 'opencv-contrib-python>=4.8.0')

    # ---------------------------------------------------------------- 3) icon
    Step '3/7 Generating application icon'
    Run $vpy @('app_icon.py')
    if (-not (Test-Path 'assets\CanonScanPro.ico')) { Fail 'icon was not generated' }

    # ------------------------------------------------- 4) bundled third-party assets
    Step '4/7 Checking bundled OCR engine, language data and AI model'
    New-Item -ItemType Directory -Force -Path 'installers', 'assets\tessdata', 'assets\models', 'release' | Out-Null
    $tessUrl = 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe'
    $tessSha = 'bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4'
    $tessExe = 'installers\tesseract-ocr-w64-setup.exe'
    if (-not (Test-Path $tessExe)) { Download $tessUrl $tessExe }
    $h = (Get-FileHash $tessExe -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $tessSha) { Fail "Tesseract installer checksum mismatch ($h). Delete installers\*.exe and run again." }
    foreach ($l in 'ara', 'fra', 'eng') {
        $f = "assets\tessdata\$l.traineddata"
        if (-not (Test-Path $f) -or (Get-Item $f).Length -lt 1MB) { Download "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/$l.traineddata" $f }
        if ((Get-Item $f).Length -lt 1MB) { Fail "$f looks incomplete" }
    }
    $model = 'assets\models\ESPCN_x2.pb'
    if (-not (Test-Path $model) -or (Get-Item $model).Length -lt 50000) { Download 'https://github.com/fannymonori/TF-ESPCN/raw/master/export/ESPCN_x2.pb' $model }
    if ((Get-Item $model).Length -lt 50000) { Fail 'AI model download failed' }

    # ---------------------------------------------------------------- 5) PyInstaller
    Step '5/7 Building the application with PyInstaller (a few minutes)'
    if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
    if (Test-Path 'dist')  { Remove-Item -Recurse -Force 'dist' }
    Run $vpy @('-m', 'PyInstaller', '--noconfirm', '--clean', 'CanonScanPro.spec')
    $exe = 'dist\CanonScanPro\CanonScanPro.exe'
    if (-not (Test-Path $exe)) { Fail 'PyInstaller did not produce CanonScanPro.exe' }

    # ---------------------------------------------------------------- 6) self-test
    Step '6/7 Self-test of the packaged EXE'
    $st = Join-Path $PSScriptRoot 'build_selftest.txt'
    if (Test-Path $st) { Remove-Item $st -Force }
    $p = Start-Process -FilePath $exe -ArgumentList @('--selftest', "`"$st`"") -Wait -PassThru
    if (Test-Path $st) { Get-Content $st | ForEach-Object { Write-Host "    $_" } }
    if ($p.ExitCode -ne 0) { Fail 'Self-test FAILED - the package is missing something (see lines above).' }

    # ---------------------------------------------------------------- 7) installer
    Step '7/7 Creating the installer (Inno Setup)'
    function Find-Iscc {
        $c = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
        foreach ($x in $c) { if (Test-Path $x) { return $x } }
        $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        return $null
    }
    $iscc = Find-Iscc
    if (-not $iscc) {
        Write-Host '    Inno Setup not found - installing it silently...'
        $ok = $false
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            & winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
            $iscc = Find-Iscc
        }
        if (-not $iscc) {
            $tmp = Join-Path $env:TEMP 'innosetup.exe'
            Download 'https://jrsoftware.org/download.php/is.exe' $tmp
            Start-Process -FilePath $tmp -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-' -Wait
            $iscc = Find-Iscc
        }
    }
    if (-not $iscc) { Fail 'Inno Setup could not be installed. Install it from jrsoftware.org and run this script again.' }
    Run $iscc @('installer.iss')

    $setup = Get-ChildItem 'release\CanonScanPro_Setup_*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $setup) { Fail 'Installer was not created' }
    Compress-Archive -Path 'dist\CanonScanPro\*' -DestinationPath 'release\CanonScanPro_Portable.zip' -Force
    $sha = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash
    Set-Content -Path "release\$($setup.Name).sha256.txt" -Value "$sha  $($setup.Name)"

    Write-Host ""
    Write-Host "SUCCESS" -ForegroundColor Green
    Write-Host "  Give the client:  $($setup.FullName)"
    Write-Host "  (portable build: release\CanonScanPro_Portable.zip)"
    Stop-Transcript | Out-Null
    exit 0
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Stop-Transcript | Out-Null
    exit 1
}
