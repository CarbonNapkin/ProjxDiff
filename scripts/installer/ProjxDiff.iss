; Inno Setup script for Projx Diff.
;
; Compiled by the release workflow (release.yml) on the Windows runner:
;   ISCC.exe /DAppVersion=<x.y.z> scripts\installer\ProjxDiff.iss
; after PyInstaller has produced the onedir build at dist\ProjxDiff-app\
; (PROJX_ONEDIR=1 — the exe plus its runtime in _internal\). Produces
; dist\ProjxDiff-setup.exe: Program Files install, Start Menu entry,
; optional desktop shortcut, and a clean uninstaller. Shipping onedir means
; launch does no self-extraction into %TEMP%, which removes the
; antivirus-vs-extraction race a onefile exe hits on first run.
;
; Deliberately NOT registered: any .driveprojx file association — that
; extension belongs to DriveWorks itself and the installer must never
; steal it.
;
; Paths are relative to this file (scripts/installer/), hence the ..\..\ hops.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

; The AppId again as a preprocessor symbol: the Pascal section at the bottom
; has to build the uninstall registry key name from it by hand. Keep in step
; with the AppId line below.
#define AppGuid "{8B6F1A20-6B2E-4C9A-9D3B-6C51F1D2A7E4}"
#define NightlyTaskName "ProjxDiff Nightly Sync"

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
; 64-bit install mode so the default lands in Program Files, not (x86).
ArchitecturesInstallIn64BitMode=x64compatible
; Admin install to Program Files by default; the dialog lets a non-admin
; choose a per-user install instead.
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Wipe the bundled runtime from any previous version before laying down the
; new one, so an upgrade can never mix old and new DLLs.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\ProjxDiff-app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Driven by the Pascal section only; never lands in the install folder.
Source: "verify_nightly_task.ps1"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Projx Diff"; Filename: "{app}\ProjxDiff.exe"
Name: "{autodesktop}\Projx Diff"; Filename: "{app}\ProjxDiff.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ProjxDiff.exe"; Description: "{cm:LaunchProgram,Projx Diff}"; Flags: nowait postinstall skipifsilent

[Code]

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppGuid}_is1';

// --------------------------------------------------------------------------
// Remove a stale 32-bit install left behind by pre-1.5.x.
//
// 1.3.0 predates ArchitecturesInstallIn64BitMode, so it registered in 32-bit
// mode: its uninstall key lives under WOW6432Node and its install folder
// resolved to Program Files (x86). Inno treats that as a *different* install,
// so every upgrade since has installed alongside it rather than superseding
// it -- the setup log says "Detected previous administrative 32-bit install?
// Yes" and then only renames the display name so Add/Remove does not show two
// identical-looking entries.
//
// On its own that is wasted disk and a confusing second entry. It turns
// dangerous next to a nightly task pointing into the x86 path, which then
// keeps running 1.3.0 forever after a clean-looking upgrade.
// --------------------------------------------------------------------------

function OldRootKey: Integer;
begin
  if IsAdminInstallMode then
    Result := HKLM32
  else
    Result := HKCU32;
end;

procedure RemoveStaleThirtyTwoBitInstall;
var
  Cmd, Exe, Params, Where: String;
  ResultCode, Waited, Split: Integer;
begin
  // Only when THIS install is the 64-bit one. In 32-bit install mode that key
  // is our own, and uninstalling ourselves mid-install would be spectacular.
  if not Is64BitInstallMode then
    Exit;

  if not RegQueryStringValue(OldRootKey, UninstallKey, 'QuietUninstallString', Cmd) then
    Cmd := '';
  if Cmd = '' then
  begin
    if not RegQueryStringValue(OldRootKey, UninstallKey, 'UninstallString', Cmd) then
      Exit;
    if Cmd = '' then
      Exit;
    // UninstallString is the bare quoted path; add the silent switches.
    Cmd := Cmd + ' /VERYSILENT /SUPPRESSMSGBOXES /NORESTART';
  end;

  // Never uninstall something living where we are about to install. A site
  // that had pointed a 32-bit install at this same folder would otherwise
  // have it deleted out from under the files we just laid down.
  if RegQueryStringValue(OldRootKey, UninstallKey, 'InstallLocation', Where) then
    if CompareText(RemoveBackslashUnlessRoot(RemoveQuotes(Where)),
                   RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) = 0 then
    begin
      Log('The 32-bit install is in this same folder; leaving it alone.');
      Exit;
    end;

  Log('Found a 32-bit Projx Diff install; removing it first: ' + Cmd);

  Split := Pos('" ', Cmd);
  if Split = 0 then
  begin
    Exe := RemoveQuotes(Cmd);
    Params := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART';
  end
  else
  begin
    Exe := RemoveQuotes(Copy(Cmd, 1, Split));
    Params := Copy(Cmd, Split + 2, Length(Cmd));
  end;

  if not Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('Could not launch the 32-bit uninstaller; leaving it in place.');
    Exit;
  end;

  // Inno's uninstaller relaunches itself from the temp folder and the first
  // process returns immediately, so waiting on Exec is not enough on its own.
  // Poll for the key it deletes on the way out, bounded -- an upgrade that
  // hangs here is worse than one that leaves the orphan behind.
  Waited := 0;
  while (Waited < 60000) and RegKeyExists(OldRootKey, UninstallKey) do
  begin
    Sleep(500);
    Waited := Waited + 500;
  end;

  if RegKeyExists(OldRootKey, UninstallKey) then
    Log('The 32-bit uninstaller did not finish within 60s; continuing anyway.')
  else
    Log('Stale 32-bit install removed.');
end;

// --------------------------------------------------------------------------
// Point an existing nightly task at the copy we just installed.
//
// An upgrade replaces the files and never looks at Task Scheduler, so a site
// whose task points somewhere the upgrade did not touch upgrades
// "successfully" and keeps running the old binary at 02:00. Nothing about a
// nightly task failing is loud -- that is how three nights went by unnoticed
// on 2026-08-16.
//
// The installer is the right place because it is already elevated: this is
// exactly the operation an ordinary session cannot perform without a separate
// UAC prompt. The decisions live in verify_nightly_task.ps1, which is
// deliberately narrow -- it never creates a task, never invents a schedule,
// and refuses to touch a command it does not recognise.
// --------------------------------------------------------------------------

procedure VerifyNightlyTask;
var
  Params: String;
  ResultCode: Integer;
begin
  ExtractTemporaryFile('verify_nightly_task.ps1');
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
            ExpandConstant('{tmp}\verify_nightly_task.ps1') + '" -ExePath "' +
            ExpandConstant('{app}\ProjxDiff.exe') + '"';

  if not Exec('powershell.exe', Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('Could not run the nightly-task check; skipping it.');
    Exit;
  end;

  Log('Nightly-task check exited with ' + IntToStr(ResultCode));

  // Silent installs (CI, unattended rollouts) get the log line and nothing
  // else -- a message box in a /VERYSILENT run is a hung installer.
  if WizardSilent then
    Exit;

  case ResultCode of
    10:
      MsgBox('The "{#NightlyTaskName}" scheduled task was running an older copy '
             + 'of Projx Diff. It now runs this install; its schedule, '
             + 'arguments and settings are unchanged.', mbInformation, MB_OK);
    11:
      MsgBox('The "{#NightlyTaskName}" scheduled task exists but does not run a '
             + 'Projx Diff executable, so it was left alone.' + #13#10 + #13#10
             + 'If it should be running the nightly sync, open Projx Diff and '
             + 'use Tools > Manage Nightly Sync > Repair scheduled task.',
             mbError, MB_OK);
    12:
      MsgBox('The "{#NightlyTaskName}" scheduled task runs an older copy of '
             + 'Projx Diff and could not be updated automatically.' + #13#10 + #13#10
             + 'Open Projx Diff and use Tools > Manage Nightly Sync > Repair '
             + 'scheduled task.', mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    RemoveStaleThirtyTwoBitInstall
  else if CurStep = ssPostInstall then
    VerifyNightlyTask;
end;
