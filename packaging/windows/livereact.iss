; Inno Setup script for LiveReact (docs/APP_ROADMAP.md item 32).
; Packs the PyInstaller one-folder build (dist\LiveReact) into a per-user installer:
;     iscc /DAppVersion=0.1.0 packaging\windows\livereact.iss
; Signing (Azure Trusted Signing or an OV certificate) happens in the release workflow, item 34.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{D9B2473F-87A2-4E4C-B408-E8DF14405F61}
AppName=LiveReact
AppVersion={#AppVersion}
AppPublisher=LiveReact
DefaultDirName={autopf}\LiveReact
DefaultGroupName=LiveReact
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist\installer
OutputBaseFilename=LiveReact-{#AppVersion}-windows-x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=LiveReact
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\LiveReact\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LiveReact"; Filename: "{app}\LiveReact.exe"
Name: "{autodesktop}\LiveReact"; Filename: "{app}\LiveReact.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LiveReact.exe"; Description: "{cm:LaunchProgram,LiveReact}"; Flags: nowait postinstall skipifsilent

; The user's rigs, fixtures, manuals and settings live in %LOCALAPPDATA%\LiveReact
; (livereact/userdata.py) and are deliberately left in place on uninstall.
