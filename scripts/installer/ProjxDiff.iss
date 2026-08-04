; Inno Setup script for Projx Diff.
;
; Compiled by the release workflow (release.yml) on the Windows runner:
;   ISCC.exe /DAppVersion=<x.y.z> scripts\installer\ProjxDiff.iss
; after PyInstaller has produced dist\ProjxDiff.exe. Produces
; dist\ProjxDiff-setup.exe: Program Files install, Start Menu entry,
; optional desktop shortcut, and a clean uninstaller.
;
; Deliberately NOT registered: any .driveprojx file association — that
; extension belongs to DriveWorks itself and the installer must never
; steal it.
;
; Paths are relative to this file (scripts/installer/), hence the ..\..\ hops.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Stable GUID so upgrades replace the existing install instead of stacking.
AppId={{8B6F1A20-6B2E-4C9A-9D3B-6C51F1D2A7E4}
AppName=Projx Diff
AppVersion={#AppVersion}
AppVerName=Projx Diff {#AppVersion}
AppPublisher=Base 10 Consultants
AppPublisherURL=https://base10consultants.com/tools/projx-diff/
AppSupportURL=https://github.com/CarbonNapkin/ProjxDiff
DefaultDirName={autopf}\Projx Diff
DefaultGroupName=Projx Diff
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=ProjxDiff-setup
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\ProjxDiff.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Admin install to Program Files by default; the dialog lets a non-admin
; choose a per-user install instead.
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\ProjxDiff.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Projx Diff"; Filename: "{app}\ProjxDiff.exe"
Name: "{autodesktop}\Projx Diff"; Filename: "{app}\ProjxDiff.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ProjxDiff.exe"; Description: "{cm:LaunchProgram,Projx Diff}"; Flags: nowait postinstall skipifsilent
