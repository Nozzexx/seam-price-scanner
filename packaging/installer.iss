; S.E.A.M. Price Scanner installer (Inno Setup 6).
; Built by build-exe.bat after PyInstaller:  ISCC /DAppVersion=1.3.1 packaging\installer.iss
;
; Installs for the current user (no administrator prompt) into
; %LOCALAPPDATA%\Programs\SEAM Price Scanner, with a Start menu entry, an
; optional desktop shortcut and an uninstaller in Settings > Apps. Settings and
; the saved sign-in live in %APPDATA%\SEAM\scanner and are kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "SEAM Price Scanner"
#define AppExe "SEAM Price Scanner.exe"

[Setup]
; Never change AppId: it is how Windows recognises an upgrade of the same app.
AppId={{0311B35F-6816-46CC-9AC7-0B92A1EB546D}
AppName=S.E.A.M. Price Scanner
AppVersion={#AppVersion}
AppVerName=S.E.A.M. Price Scanner {#AppVersion}
AppPublisher=S.E.A.M. Industries
AppPublisherURL=https://www.seamindustries.com/scanner
AppSupportURL=https://github.com/Nozzexx/seam-price-scanner
AppUpdatesURL=https://github.com/Nozzexx/seam-price-scanner/releases
VersionInfoVersion={#AppVersion}
VersionInfoCompany=S.E.A.M. Industries
VersionInfoDescription=S.E.A.M. Price Scanner setup
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName=S.E.A.M. Price Scanner
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
SetupIconFile=..\assets\seam.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName=S.E.A.M. Price Scanner
OutputDir=..\dist
OutputBaseFilename=SEAM-Price-Scanner-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Close a running scanner before upgrading, and start it again afterwards.
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; Everything PyInstaller built. Old files from a previous version are removed first.
Source: "..\dist\SEAM Price Scanner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\S.E.A.M. Price Scanner"; Filename: "{app}\{#AppExe}"; Comment: "Read commodity terminal prices and report them to S.E.A.M."
Name: "{autodesktop}\S.E.A.M. Price Scanner"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start the S.E.A.M. Price Scanner"; Flags: nowait postinstall skipifsilent
