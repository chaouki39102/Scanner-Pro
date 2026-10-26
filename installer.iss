; Canon Scan Pro - Inno Setup 6 script
; Installs the app, then silently installs the Tesseract OCR engine (if missing)
; and copies the Arabic / French / English language data. No Python needed on the client PC.

#define AppName    "Canon Scan Pro"
#define AppVersion "7.3.0"
#define AppExe     "CanonScanPro.exe"

[Setup]
AppId={{7D2C3A94-5B1E-4F7A-9A66-C0A0A5CA11F0}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\Canon Scan Pro
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=release
OutputBaseFilename=CanonScanPro_Setup_{#AppVersion}
SetupIconFile=assets\CanonScanPro.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\CanonScanPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Embedded in the setup but only extracted on demand by the [Code] section
Source: "installers\tesseract-ocr-w64-setup.exe"; Flags: dontcopy
Source: "assets\tessdata\ara.traineddata"; Flags: dontcopy
Source: "assets\tessdata\fra.traineddata"; Flags: dontcopy
Source: "assets\tessdata\eng.traineddata"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
function FindTesseractDir(): String;
var
  Dir: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM64, 'SOFTWARE\Tesseract-OCR', 'InstallDir', Dir) and
     FileExists(AddBackslash(Dir) + 'tesseract.exe') then
  begin
    Result := RemoveBackslash(Dir);
    Exit;
  end;
  Dir := ExpandConstant('{commonpf64}\Tesseract-OCR');
  if FileExists(Dir + '\tesseract.exe') then Result := Dir;
end;

procedure InstallOcrEngine();
var
  TessDir, DataDir, Lang: String;
  Langs: array[0..2] of String;
  I, Rc: Integer;
begin
  TessDir := FindTesseractDir();
  if TessDir = '' then
  begin
    WizardForm.StatusLabel.Caption := 'Installing OCR engine (Tesseract)...';
    ExtractTemporaryFile('tesseract-ocr-w64-setup.exe');
    if not Exec(ExpandConstant('{tmp}\tesseract-ocr-w64-setup.exe'), '/S', '', SW_HIDE, ewWaitUntilTerminated, Rc) then
      Log('Tesseract silent install could not start: ' + SysErrorMessage(Rc))
    else
      Log('Tesseract silent install exit code: ' + IntToStr(Rc));
    TessDir := FindTesseractDir();
  end;

  if TessDir = '' then
  begin
    SuppressibleMsgBox('OCR engine (Tesseract) could not be installed automatically.' + #13#10 +
      'The scanner and all other features work normally. OCR needs Tesseract installed later.',
      mbInformation, MB_OK, IDOK);
    Exit;
  end;

  WizardForm.StatusLabel.Caption := 'Installing Arabic / French language data...';
  DataDir := TessDir + '\tessdata';
  ForceDirectories(DataDir);
  Langs[0] := 'ara'; Langs[1] := 'fra'; Langs[2] := 'eng';
  for I := 0 to 2 do
  begin
    Lang := Langs[I] + '.traineddata';
    ExtractTemporaryFile(Lang);
    if not FileCopy(ExpandConstant('{tmp}\') + Lang, DataDir + '\' + Lang, False) then
      Log('Could not copy ' + Lang + ' to ' + DataDir);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then InstallOcrEngine();
end;
