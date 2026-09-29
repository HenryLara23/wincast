; Wincast installer (Inno Setup 6.3+).
;
; Built by tools/build_exe.py --installer (and by the release workflow):
;     ISCC /DAppVersion=16.20 installer\wincast.iss
; Packs dist\Wincast\ into dist\Wincast-<version>-setup.exe.
;
; Installs for the current user by default (%LOCALAPPDATA%\Programs\Wincast, no
; admin prompt); a dialog offers "all users" (Program Files, needs admin).
; The app keeps everything it writes in %LOCALAPPDATA%\Wincast plus its settings
; under HKCU\Software\Wincast, never in its install folder.
;
; Uninstall: closes Wincast, removes the start-with-Windows value if it points at
; this install, and asks whether to delete the history, models and settings too.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif
#ifndef AppDir
  #define AppDir "..\dist\Wincast"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
; never change AppId: it is how Windows knows a new version replaces the old one
AppId={{84181F54-477F-4EE5-978B-7526BB83F408}
AppName=Wincast
AppVersion={#AppVersion}
AppVerName=Wincast {#AppVersion}
AppPublisher=Henry Lara
AppPublisherURL=https://github.com/HenryLara23/wincast
AppSupportURL=https://github.com/HenryLara23/wincast/issues
AppUpdatesURL=https://github.com/HenryLara23/wincast/releases
DefaultDirName={autopf}\Wincast
DefaultGroupName=Wincast
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=Wincast-{#AppVersion}-setup
SetupIconFile=..\src\wincast\resources\icon\wincast.ico
UninstallDisplayIcon={app}\Wincast.exe
UninstallDisplayName=Wincast
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; a running Wincast is closed before its files are replaced
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; an update replaces the whole bundled runtime, so no stale files from the old version remain
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Wincast"; Filename: "{app}\Wincast.exe"
Name: "{autodesktop}\Wincast"; Filename: "{app}\Wincast.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Wincast.exe"; Description: "{cm:LaunchProgram,Wincast}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im Wincast.exe"; Flags: runhidden; RunOnceId: "StopWincast"

[Code]
var
  DeleteUserData: Boolean;

{ Remove the start-with-Windows value, but only if it starts THIS copy
  (a portable copy elsewhere keeps its own). }
procedure RemoveAutostart;
var
  Value: String;
begin
  if RegQueryStringValue(HKCU, '{#RunKey}', 'Wincast', Value) then
    if Pos(Lowercase(ExpandConstant('{app}\Wincast.exe')), Lowercase(Value)) > 0 then
      RegDeleteValue(HKCU, '{#RunKey}', 'Wincast');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    DeleteUserData := (not UninstallSilent) and
      (MsgBox('Also delete your Wincast data?' + #13#10 + #13#10 +
              'This is your game history, downloaded models and settings, in' + #13#10 +
              ExpandConstant('{localappdata}\Wincast') + #13#10 + #13#10 +
              'Choose No to keep them for a later reinstall.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES);
    RemoveAutostart;
  end;
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then
  begin
    DelTree(ExpandConstant('{localappdata}\Wincast'), True, True, True);
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Wincast');
  end;
end;
