; Inno Setup script -- per-user installer, no admin rights needed.
; {app} (the install directory) is created automatically by Inno Setup;
; app_data.db is created there by the app itself on first run (core/db.py
; resolves it next to the running .exe), so per-user install location keeps
; that write always permitted (unlike Program Files, which needs admin).

#define MyAppName "Aiποδείξεις (AIReceipts)"
#define MyAppVersion "1.0"
#define MyAppExeName "AIReceipts.exe"

[Setup]
AppId={{B36F1C9E-6E2E-4B1A-9C3D-8E9E7C1E9B21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\AIReceipts
DefaultGroupName=AIReceipts
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=AIReceipts-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Δημιουργία εικονιδίου στην Επιφάνεια εργασίας"; GroupDescription: "Πρόσθετα εικονίδια:"

[Files]
Source: "dist\AIReceipts.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AIReceipts"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Απεγκατάσταση AIReceipts"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AIReceipts"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Εκκίνηση AIReceipts"; Flags: nowait postinstall skipifsilent
