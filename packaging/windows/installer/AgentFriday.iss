; ============================================================================
;  Agent Friday Beta 1.0 - Windows setup program (Inno Setup 6.3 or newer)
;
;  Built by packaging\windows\build-installer.ps1, which stages the payload and
;  passes the defines below. Do not compile this by hand without them.
;
;  What the program is:
;    * one file, per-user, no administrator prompt, no telemetry, unsigned;
;    * it carries its own Python, Friday's files and the pre-built wheels, and
;      unpacks them to a temporary folder;
;    * the engine that installs them is install.ps1 (unchanged in method: every
;      step verified, never trusting an exit code). This script owns the
;      wizard, the shortcuts, the autostart entry, the Add/Remove Programs
;      entry and the uninstaller;
;    * it NEVER downloads model weights. The model page records the choice in
;      first-run.json and Friday's own downloader fetches the files on first
;      start, with progress, resume and sha256 verification.
;
;  Releases are ordered by BUILD SEQUENCE, not version number: Beta 1.0 is
;  1.0.0b1, numerically below the 5.x line it replaces. The arithmetic below
;  restates src\agent_friday\release.py; a test holds the two together.
;
;  Silent install, for CI and managed machines:
;    AgentFriday-Setup-<tag>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=setup.log
;      /ModelsCloud=1                 use a cloud model; download nothing (the default)
;      /ModelFast=<id> [/ModelFastPacking=<p>] /ModelDeep=<id> [/ModelDeepPacking=<p>]
;      /ConsentDownload=1             required with /ModelFast and /ModelDeep
;      /SkipMemory=1 /SkipJudgment=1  skip the large optional parts
;      /AllowNetwork=1                let pip use the internet as well as the carried wheels
;  Silent uninstall keeps the person's data unless /REMOVEDATA=1 is given.
; ============================================================================

#ifndef AppVersion
  #error AppVersion is not defined. Build with packaging\windows\build-installer.ps1.
#endif
#ifndef AppTag
  #error AppTag is not defined. Build with packaging\windows\build-installer.ps1.
#endif
#ifndef BuildSequence
  #error BuildSequence is not defined. Build with packaging\windows\build-installer.ps1.
#endif
#ifndef AppFileVersion
  #define AppFileVersion "1.0.0.0"
#endif
#ifndef StageDir
  #error StageDir is not defined. Build with packaging\windows\build-installer.ps1.
#endif
#ifndef RepoRoot
  #define RepoRoot "..\..\.."
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

#define AppName "Agent Friday"
#define ReleaseName "Agent Friday Beta 1.0"
#define AppPublisher "FutureSpeak.AI"
#define AppGuid "BE782F40-3D1C-4F12-8635-4F7561938F75"
#define UninstKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\{" + AppGuid + "}_is1"
#define LegacyUninstKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\AgentFriday"

[Setup]
AppId={{{#AppGuid}}
AppName={#ReleaseName}
AppVersion={#AppTag}
AppVerName={#ReleaseName}
AppPublisher={#AppPublisher}
AppPublisherURL=https://futurespeak.ai
AppSupportURL=https://github.com/FutureSpeakAI/Agent-Friday/issues
AppUpdatesURL=https://github.com/FutureSpeakAI/Agent-Friday/releases
VersionInfoVersion={#AppFileVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#ReleaseName}
VersionInfoDescription={#ReleaseName} setup
DefaultDirName={localappdata}\AgentFriday
DisableDirPage=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=AgentFriday-Setup-{#AppTag}
SetupIconFile={#RepoRoot}\assets\icons\futurespeak.ico
UninstallDisplayIcon={app}\AgentFriday.ico
UninstallDisplayName={#ReleaseName}
LicenseFile={#RepoRoot}\LICENSE
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no
RestartApplications=no
UsePreviousTasks=yes
DisableReadyPage=no
ShowLanguageDialog=no
#if FileExists(AddBackslash(SourcePath) + "wizard.bmp")
WizardImageFile=wizard.bmp
#endif
#if FileExists(AddBackslash(SourcePath) + "wizard-small.bmp")
WizardSmallImageFile=wizard-small.bmp
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start Agent Friday quietly when I sign in"; GroupDescription: "Options:"; Flags: unchecked

[Files]
; Everything install.ps1 needs, in the layout it expects, in Setup's temporary
; folder (removed when Setup exits).
Source: "{#StageDir}\*"; DestDir: "{tmp}\af-setup"; Flags: recursesubdirs createallsubdirs ignoreversion
; The one file the shortcuts, the uninstaller and the Apps list point at.
Source: "{#RepoRoot}\assets\icons\futurespeak.ico"; DestDir: "{app}"; DestName: "AgentFriday.ico"; Flags: ignoreversion
; Extracted early, for the model page.
Source: "{#RepoRoot}\packaging\windows\lib\ModelPicker.ps1"; Flags: dontcopy
Source: "probe-hardware.ps1"; Flags: dontcopy
Source: "{#RepoRoot}\src\agent_friday\resources\model_shortlist.json"; Flags: dontcopy
Source: "{#RepoRoot}\src\agent_friday\resources\voice_front_options.json"; Flags: dontcopy

[Icons]
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\Agent Friday.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\AgentFriday.ico"; Comment: "Start Agent Friday"
Name: "{userprograms}\{#AppName}\{#AppName}"; Filename: "{app}\Agent Friday.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\AgentFriday.ico"; Comment: "Start Agent Friday"
Name: "{userprograms}\{#AppName}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\AgentFriday.ico"; Comment: "Remove Agent Friday from this computer"
Name: "{userprograms}\{#AppName}\Start Friday when I sign in"; Filename: "{app}\Start Friday when I sign in.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\AgentFriday.ico"; Comment: "Turn the automatic start on or off"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\Agent Friday (background).cmd"; WorkingDir: "{app}"; IconFilename: "{app}\AgentFriday.ico"; Comment: "Start Agent Friday quietly when you sign in"; Tasks: autostart

[InstallDelete]
; The autostart entry used to be called "FRIDAY Desktop". Remove the old one on
; upgrade; and honour a "no" to autostart over one a previous install left.
Type: files; Name: "{userstartup}\FRIDAY Desktop.lnk"
Type: files; Name: "{userstartup}\Friday Desktop.lnk"
Type: files; Name: "{userstartup}\{#AppName}.lnk"; Tasks: not autostart

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "FRIDAY Desktop"; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "FridayDesktop"; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "AgentFriday"; Flags: deletevalue

[Run]
Filename: "{app}\Agent Friday.cmd"; Description: "Start Agent Friday now"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; install.ps1 puts Python, logs and caches here; none of it is the person's data.
Type: filesandordirs; Name: "{app}"

[Code]
type
  TModelOption = record
    Seat, Id, Packing, Caption, SizeText, Note, PickJson: String;
    Bytes: Int64;
    Recommended, Older, Companions: Boolean;
  end;
  // An option that does not fit this computer: listed greyed, with why.
  TUnfitOption = record
    Seat, Caption, SizeText, Reason: String;
  end;

const
  // BEGIN SEQUENCE ARITHMETIC (agent_friday/release.py; held equal by a test)
  EraFloor = 100000000;
  LegacyMajorLow = 2;
  LegacyMajorHigh = 5;
  // END SEQUENCE ARITHMETIC
  MegaByte = 1048576;
  DiskFloorMib = 10240;
  RuntimeAllowanceMib = 650;
  // install.ps1's preflight estimate for Agent Friday itself (held equal by a test).
  FridayDiskMib = 8192;

var
  ModelPage: TWizardPage;
  HwLabel, NoteLabel, TotalLabel: TNewStaticText;
  CloudCheck, ConsentCheck: TNewCheckBox;
  ModelList: TNewCheckListBox;
  DeepItem, DeepOpt, FastItem, FastOpt: array of Integer;
  ForcedCloud: Boolean;
  Opts: array of TModelOption;
  Unfit: array of TUnfitOption;
  FactRam, FactDisk, FactVram: Int64;
  FactGpu, FactVramKnown, TierId, ProbeNotes: String;
  ProbeStarted, ProbeOk: Boolean;
  PrevVersion: String;
  PrevSequence: Int64;
  RemoveEverything: Boolean;
  ShortcutNote: String;
  ChosenCloud, ChosenConsent, ChosenDeepComp: Boolean;
  ChosenFast, ChosenFastPack, ChosenDeep, ChosenDeepPack, ChosenDeepPick, ChosenTier: String;
  ChosenTotal: Int64;

// ---------------------------------------------------------------------------
//  Small helpers
// ---------------------------------------------------------------------------

function PowerShellExe(): String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function NextField(var S: String): String;
var
  P: Integer;
begin
  P := Pos('|', S);
  if P = 0 then
  begin
    Result := S;
    S := '';
  end
  else
  begin
    Result := Copy(S, 1, P - 1);
    Delete(S, 1, P);
  end;
end;

function JsonEscape(S: String): String;
var
  I: Integer;
  C: String;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := Copy(S, I, 1);
    if C = '\' then
      Result := Result + '\\'
    else if C = '"' then
      Result := Result + '\"'
    else if Ord(S[I]) < 32 then
      Result := Result + ' '
    else
      Result := Result + C;
  end;
end;

function BoolJson(B: Boolean): String;
begin
  if B then Result := 'true' else Result := 'false';
end;

function GbText(Bytes: Int64): String;
var
  Tenths: Int64;
begin
  Tenths := (Bytes * 10 + 536870912) div 1073741824;
  Result := IntToStr(Tenths div 10) + '.' + IntToStr(Tenths mod 10) + ' GB';
end;

procedure WriteSetupLog(Msg: String);
var
  Dir: String;
begin
  Log(Msg);
  Dir := ExpandConstant('{app}\logs');
  try
    ForceDirectories(Dir);
    SaveStringToFile(Dir + '\setup.log', GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '  ' + Msg + #13#10, True);
  except
    Log('Could not write the setup log: ' + GetExceptionMessage);
  end;
end;

// ---------------------------------------------------------------------------
//  Which release is newer: by build sequence, never by version number
//  (the same ordering as agent_friday/release.py)
// ---------------------------------------------------------------------------

function IsDigit(C: String): Boolean;
begin
  Result := (Length(C) = 1) and (C >= '0') and (C <= '9');
end;

function IsLetter(C: String): Boolean;
begin
  Result := (Length(C) = 1) and (((C >= 'a') and (C <= 'z')) or ((C >= 'A') and (C <= 'Z')));
end;

// Drops leading blanks and line breaks (the text after a JSON colon).
function LeftTrim(S: String): String;
begin
  while (Length(S) > 0) and ((Copy(S, 1, 1) = ' ') or (Copy(S, 1, 1) = #9) or (Copy(S, 1, 1) = #13) or (Copy(S, 1, 1) = #10)) do
    Delete(S, 1, 1);
  Result := S;
end;

// 5.14.3 -> 51403 (unlabelled 2.x through 5.x are the old line); 1.0.0b1 or 1.0.0-beta.1 -> EraFloor + 1000000 + 1; 0 when
// the text is not a version.
function SequenceForVersion(Raw: String): Int64;
var
  S, Part, Lab: String;
  Group: Integer;
  Nums: array[0..2] of Int64;
  Number, Stage, Base: Int64;
  HasLabel: Boolean;
begin
  Result := 0;
  S := LeftTrim(Raw);
  if (Length(S) > 0) and ((S[1] = 'v') or (S[1] = 'V')) then Delete(S, 1, 1);
  if (Length(S) = 0) or (not IsDigit(Copy(S, 1, 1))) then Exit;

  Nums[0] := 0; Nums[1] := 0; Nums[2] := 0;
  Group := 0;
  while (Group < 3) and (Length(S) > 0) and IsDigit(Copy(S, 1, 1)) do
  begin
    Part := '';
    while (Length(S) > 0) and IsDigit(Copy(S, 1, 1)) do
    begin
      Part := Part + Copy(S, 1, 1);
      Delete(S, 1, 1);
    end;
    Nums[Group] := StrToInt64Def(Part, 0);
    Group := Group + 1;
    if (Length(S) > 1) and (Copy(S, 1, 1) = '.') and IsDigit(Copy(S, 2, 1)) and (Group < 3) then
      Delete(S, 1, 1)
    else
      Break;
  end;

  // What follows: nothing, a pre-release label (b1, -beta.2, rc1) or build metadata.
  HasLabel := False;
  Lab := '';
  Number := 0;
  if (Length(S) > 0) and ((Copy(S, 1, 1) = '-') or (Copy(S, 1, 1) = '.')) then
  begin
    if (Length(S) > 1) and IsLetter(Copy(S, 2, 1)) then Delete(S, 1, 1);
  end;
  if (Length(S) > 0) and IsLetter(Copy(S, 1, 1)) then
  begin
    HasLabel := True;
    while (Length(S) > 0) and IsLetter(Copy(S, 1, 1)) do
    begin
      Lab := Lab + Lowercase(Copy(S, 1, 1));
      Delete(S, 1, 1);
    end;
    if (Length(S) > 0) and ((Copy(S, 1, 1) = '-') or (Copy(S, 1, 1) = '.')) then Delete(S, 1, 1);
    Part := '';
    while (Length(S) > 0) and IsDigit(Copy(S, 1, 1)) do
    begin
      Part := Part + Copy(S, 1, 1);
      Delete(S, 1, 1);
    end;
    Number := StrToInt64Def(Part, 0);
  end;

  if (not HasLabel) and (Nums[0] >= LegacyMajorLow) and (Nums[0] <= LegacyMajorHigh) then
  begin
    if Nums[1] > 99 then Nums[1] := 99;
    if Nums[2] > 99 then Nums[2] := 99;
    Result := Nums[0] * 10000 + Nums[1] * 100 + Nums[2];
    Exit;
  end;

  if HasLabel then
  begin
    Base := 1;
    if (Lab = 'rc') or (Lab = 'c') then Base := 50;
    Stage := Base + Number - 1;
    if Number < 1 then Stage := Base;
    if (Base = 1) and (Stage > 49) then Stage := 49;
    if (Base <> 1) and (Stage > 98) then Stage := 98;
  end
  else
    Stage := 99;
  Result := EraFloor + Nums[0] * 1000000 + Nums[1] * 10000 + Nums[2] * 100 + Stage;
end;

function JsonText(Json, Key: String): String;
var
  P: Integer;
  Rest: String;
begin
  Result := '';
  P := Pos('"' + Key + '"', Json);
  if P = 0 then Exit;
  Rest := Copy(Json, P + Length(Key) + 2, Length(Json));
  P := Pos(':', Rest);
  if P = 0 then Exit;
  Delete(Rest, 1, P);
  Rest := LeftTrim(Rest);
  if (Length(Rest) > 0) and (Copy(Rest, 1, 1) = '"') then
  begin
    Delete(Rest, 1, 1);
    P := Pos('"', Rest);
    if P > 0 then Result := Copy(Rest, 1, P - 1);
  end
  else
  begin
    P := 1;
    while (P <= Length(Rest)) and IsDigit(Copy(Rest, P, 1)) do P := P + 1;
    Result := Copy(Rest, 1, P - 1);
  end;
end;

// The folder an earlier install lives in: this program's own, else the 5.x
// installer's, else the default.
function ExistingInstallRoot(): String;
var
  Value: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, '{#UninstKey}', 'Inno Setup: App Path', Value) and (Value <> '') then
    Result := Value
  else if RegQueryStringValue(HKCU, '{#LegacyUninstKey}', 'InstallLocation', Value) and (Value <> '') then
    Result := Value
  else
    Result := ExpandConstant('{localappdata}\AgentFriday');
end;

// Sets PrevVersion and returns the installed build sequence, or 0 for none.
function DetectInstalledSequence(): Int64;
var
  Root, Text, Seq, Ver: String;
  AnsiText: AnsiString;
begin
  Result := 0;
  PrevVersion := '';
  Root := ExistingInstallRoot();
  if LoadStringFromFile(Root + '\install-manifest.json', AnsiText) then
  begin
    Text := String(AnsiText);
    Seq := JsonText(Text, 'build_sequence');
    Ver := JsonText(Text, 'version');
    if Ver <> '' then PrevVersion := Ver;
    if Seq <> '' then
    begin
      Result := StrToInt64Def(Seq, 0);
      if Result > 0 then Exit;
    end;
    if Ver <> '' then
    begin
      Result := SequenceForVersion(Ver);
      if Result > 0 then Exit;
    end;
  end;
  if RegQueryStringValue(HKCU, '{#LegacyUninstKey}', 'DisplayVersion', Ver) and (Ver <> '') then
  begin
    PrevVersion := Ver;
    Result := SequenceForVersion(Ver);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  PrevSequence := DetectInstalledSequence();
  if PrevSequence > {#BuildSequence} then
  begin
    SuppressibleMsgBox('A newer version of Agent Friday' + ' (' + PrevVersion + ') is already installed on this computer.'#13#10#13#10 +
      'This setup is older, so it will not replace it. Nothing was changed.', mbInformation, MB_OK, IDOK);
    Result := False;
  end;
end;

// ---------------------------------------------------------------------------
//  The model page
// ---------------------------------------------------------------------------

function LoadProbe(): Boolean;
var
  Lines: TArrayOfString;
  I, N, Code: Integer;
  L, Kind, Field, Value, OutFile, Params: String;
  O: TModelOption;
  U: TUnfitOption;
begin
  Result := False;
  ProbeNotes := '';
  TierId := '';
  FactRam := 0; FactDisk := 0; FactVram := 0;
  FactGpu := ''; FactVramKnown := 'False';
  SetArrayLength(Opts, 0);
  SetArrayLength(Unfit, 0);
  try
    ExtractTemporaryFile('ModelPicker.ps1');
    ExtractTemporaryFile('probe-hardware.ps1');
    ExtractTemporaryFile('model_shortlist.json');
    ExtractTemporaryFile('voice_front_options.json');
    OutFile := ExpandConstant('{tmp}\model-options.txt');
    DeleteFile(OutFile);
    Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\probe-hardware.ps1') + '"' +
      ' -OutFile "' + OutFile + '" -ShortlistPath "' + ExpandConstant('{tmp}\model_shortlist.json') + '"' +
      ' -ModelsPath "' + ExpandConstant('{%USERPROFILE}\.friday') + '"';
    if (not Exec(PowerShellExe(), Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code)) or (Code <> 0) then
    begin
      Log('The hardware probe did not run (exit ' + IntToStr(Code) + ').');
      Exit;
    end;
    if not LoadStringsFromFile(OutFile, Lines) then Exit;
  except
    Log('The hardware probe failed: ' + GetExceptionMessage);
    Exit;
  end;

  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    L := Lines[I];
    Kind := NextField(L);
    if Kind = 'FACT' then
    begin
      Field := NextField(L);
      Value := L;
      if Field = 'ram_mib' then FactRam := StrToInt64Def(Value, 0)
      else if Field = 'disk_free_mib' then FactDisk := StrToInt64Def(Value, 0)
      else if Field = 'vram_mib' then FactVram := StrToInt64Def(Value, 0)
      else if Field = 'gpu_name' then FactGpu := Value
      else if Field = 'vram_known' then FactVramKnown := Value;
    end
    else if Kind = 'TIER' then
      TierId := L
    else if Kind = 'NOTE' then
      ProbeNotes := ProbeNotes + L + ' '
    else if Kind = 'UNFIT' then
    begin
      U.Seat := NextField(L);
      NextField(L);
      U.Caption := NextField(L);
      U.SizeText := NextField(L);
      U.Reason := L;
      N := GetArrayLength(Unfit);
      SetArrayLength(Unfit, N + 1);
      Unfit[N] := U;
    end
    else if Kind = 'OPT' then
    begin
      O.Seat := NextField(L);
      O.Id := NextField(L);
      O.Packing := NextField(L);
      O.Caption := NextField(L);
      O.Bytes := StrToInt64Def(NextField(L), 0);
      O.SizeText := NextField(L);
      O.Recommended := NextField(L) = '1';
      O.Older := NextField(L) = '1';
      O.Companions := NextField(L) = '1';
      O.Note := NextField(L);
      O.PickJson := L;
      N := GetArrayLength(Opts);
      SetArrayLength(Opts, N + 1);
      Opts[N] := O;
    end;
  end;
  StringChangeEx(ProbeNotes, ' -> ', ' ' + #$2192 + ' ', True);
  Result := (TierId <> '');
end;

function RadioCaption(const O: TModelOption): String;
begin
  Result := O.Caption + ' - ' + O.SizeText;
  if O.Recommended then
    Result := Result + ' (Recommended)'
  else if O.Older then
    Result := Result + ' (earlier generation)';
end;

// The option index of the radio the person ticked in one group, or -1.
function SelectedOption(Items, OptIdx: array of Integer): Integer;
var
  I: Integer;
begin
  Result := -1;
  for I := 0 to GetArrayLength(Items) - 1 do
    if ModelList.Checked[Items[I]] then Result := OptIdx[I];
end;

function TotalBytesChosen(): Int64;
var
  D, F: Integer;
begin
  Result := 0;
  D := SelectedOption(DeepItem, DeepOpt);
  F := SelectedOption(FastItem, FastOpt);
  if D >= 0 then Result := Result + Opts[D].Bytes;
  if (F >= 0) and ((D < 0) or (Opts[F].Id <> Opts[D].Id)) then Result := Result + Opts[F].Bytes;
end;

procedure RefreshModelPage();
var
  D, F: Integer;
  Msg: String;
begin
  ModelList.Enabled := not CloudCheck.Checked;
  ConsentCheck.Enabled := not CloudCheck.Checked;
  Msg := '';
  if CloudCheck.Checked then
  begin
    TotalLabel.Caption := 'Nothing will be downloaded. After setup, open Settings and add a key for the cloud model you want to use.';
    if not ForcedCloud then NoteLabel.Caption := '';
    Exit;
  end;
  D := SelectedOption(DeepItem, DeepOpt);
  F := SelectedOption(FastItem, FastOpt);
  if D >= 0 then Msg := Opts[D].Caption + ': ' + Opts[D].Note + ' ';
  if F >= 0 then Msg := Msg + Opts[F].Caption + ': ' + Opts[F].Note;
  NoteLabel.Caption := Msg;
  if (D >= 0) and (F >= 0) then
    TotalLabel.Caption := 'Total download: ' + GbText(TotalBytesChosen()) + '. Setup itself downloads nothing: it starts the first time Agent Friday opens, shows its progress, checks every file against the publisher''s checksum, and picks up where it stopped if interrupted.'
  else
    TotalLabel.Caption := 'Choose one model for each job. Both are needed for local use.';
end;

procedure ModelChoiceChanged(Sender: TObject);
begin
  RefreshModelPage();
end;

// One group of radios in the list: the heading, then every option for the seat.
// Nothing is ticked: the recommendation is a label, never a preselection. An
// option that does not fit follows, greyed, with the reason on its own line, so a
// heading is never empty without saying why.
procedure AddSeat(Seat, Heading: String);
var
  I, N, Item, Listed: Integer;
begin
  Listed := 0;
  ModelList.AddGroup(Heading, '', 0, nil);
  for I := 0 to GetArrayLength(Opts) - 1 do
  if Opts[I].Seat = Seat then
  begin
    Item := ModelList.AddRadioButton(RadioCaption(Opts[I]), '', 1, False, True, nil);
    if Seat = 'deep_thinker' then
    begin
      N := GetArrayLength(DeepItem);
      SetArrayLength(DeepItem, N + 1);
      SetArrayLength(DeepOpt, N + 1);
      DeepItem[N] := Item;
      DeepOpt[N] := I;
    end
    else
    begin
      N := GetArrayLength(FastItem);
      SetArrayLength(FastItem, N + 1);
      SetArrayLength(FastOpt, N + 1);
      FastItem[N] := Item;
      FastOpt[N] := I;
    end;
    Listed := Listed + 1;
  end;
  for I := 0 to GetArrayLength(Unfit) - 1 do
  if Unfit[I].Seat = Seat then
  begin
    ModelList.AddRadioButton(Unfit[I].Caption + ' - ' + Unfit[I].SizeText, Unfit[I].Reason, 1, False, False, nil);
    Listed := Listed + 1;
  end;
  if Listed = 0 then
    ModelList.AddRadioButton('No local model could be checked on this computer.', '', 1, False, False, nil);
end;

procedure BuildModelChoices();
var
  Gpu: String;
begin
  WizardForm.NextButton.Enabled := False;
  HwLabel.Caption := 'Looking at this computer...';
  WizardForm.Refresh;
  ProbeOk := LoadProbe();
  WizardForm.NextButton.Enabled := True;

  if ProbeOk then
  begin
    if FactGpu = '' then
      Gpu := 'no graphics card Agent Friday can use'
    else if FactVramKnown = 'True' then
      Gpu := FactGpu + ' (' + IntToStr((FactVram + 512) div 1024) + ' GB)'
    else
      Gpu := FactGpu + ' (memory not readable, so it is not counted)';
    HwLabel.Caption := 'This computer: ' + IntToStr((FactRam + 512) div 1024) + ' GB of memory, ' + Gpu + ', ' +
      IntToStr(FactDisk div 1024) + ' GB free on this drive.';
  end
  else
    HwLabel.Caption := 'Setup could not read this computer''s hardware, so no local model is offered. You can add one later in Settings.';

  AddSeat('deep_thinker', 'Deep thinker');
  AddSeat('fast_responder', 'Fast responder (voice and quick replies)');

  if (GetArrayLength(DeepItem) = 0) or (GetArrayLength(FastItem) = 0) then
  begin
    // Nothing (or not both jobs) fits: a cloud model is the only honest option.
    ForcedCloud := True;
    CloudCheck.Checked := True;
    CloudCheck.Enabled := False;
    if ProbeNotes <> '' then
      NoteLabel.Caption := ProbeNotes
    else
      NoteLabel.Caption := 'No local model fits this computer for both jobs. You can use a cloud model now and add local models later in Settings ' + #$2192 + ' Models.';
  end;
  RefreshModelPage();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = ModelPage.ID) and (not ProbeStarted) then
  begin
    ProbeStarted := True;
    BuildModelChoices();
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  D, F: Integer;
  NeedMib: Int64;
begin
  Result := True;
  if CurPageID <> ModelPage.ID then Exit;
  // A silent install takes its models from the command line (see the WizardSilent
  // branch below, which falls back to cloud with no downloads); this page's checks
  // would only raise a box nobody can answer and stop setup.
  if WizardSilent then Exit;
  if CloudCheck.Checked then Exit;
  D := SelectedOption(DeepItem, DeepOpt);
  F := SelectedOption(FastItem, FastOpt);
  if (D < 0) or (F < 0) then
  begin
    MsgBox('Choose a model for each of the two jobs, or choose a cloud model instead.', mbInformation, MB_OK);
    Result := False;
    Exit;
  end;
  if not ConsentCheck.Checked then
  begin
    MsgBox('Tick the box to download these models when Agent Friday first starts, or choose a cloud model instead.', mbInformation, MB_OK);
    Result := False;
    Exit;
  end;
  NeedMib := TotalBytesChosen() div MegaByte + RuntimeAllowanceMib + FridayDiskMib;
  if FactDisk - NeedMib < DiskFloorMib then
  begin
    MsgBox('These two models together need ' + GbText(TotalBytesChosen()) + ' and Agent Friday itself needs about 8 GB, and that would leave this drive with less than 10 GB free. ' +
      'Choose smaller models, free some space, or choose a cloud model instead.', mbInformation, MB_OK);
    Result := False;
  end;
end;

procedure InitializeWizard();
var
  W, H, Y, ListH: Integer;
  Startup: String;
begin
  // The model page needs more room than the standard page.
  WizardForm.Height := WizardForm.Height + ScaleY(70);
  WizardForm.Top := WizardForm.Top - ScaleY(35);

  ModelPage := CreateCustomPage(wpSelectTasks, 'Choose your AI models',
    'Two jobs, two models. Models that do not fit this computer are greyed, with the reason.');
  W := ModelPage.SurfaceWidth;
  H := ModelPage.SurfaceHeight;

  // Laid out from the bottom up, so the list takes whatever room is left and
  // scrolls inside itself if there is little.
  Y := H;
  Y := Y - ScaleY(40);
  TotalLabel := TNewStaticText.Create(ModelPage);
  TotalLabel.Parent := ModelPage.Surface;
  TotalLabel.AutoSize := False;
  TotalLabel.WordWrap := True;
  TotalLabel.SetBounds(0, Y, W, ScaleY(40));

  Y := Y - ScaleY(22);
  ConsentCheck := TNewCheckBox.Create(ModelPage);
  ConsentCheck.Parent := ModelPage.Surface;
  ConsentCheck.SetBounds(0, Y, W, ScaleY(20));
  ConsentCheck.Caption := 'Download these models when Agent Friday first starts';
  ConsentCheck.OnClick := @ModelChoiceChanged;

  Y := Y - ScaleY(66);
  NoteLabel := TNewStaticText.Create(ModelPage);
  NoteLabel.Parent := ModelPage.Surface;
  NoteLabel.AutoSize := False;
  NoteLabel.WordWrap := True;
  NoteLabel.SetBounds(0, Y, W, ScaleY(64));

  Y := Y - ScaleY(24);
  CloudCheck := TNewCheckBox.Create(ModelPage);
  CloudCheck.Parent := ModelPage.Surface;
  CloudCheck.SetBounds(0, Y, W, ScaleY(20));
  CloudCheck.Caption := 'Use a cloud model instead (set up a key after install)';
  CloudCheck.OnClick := @ModelChoiceChanged;

  HwLabel := TNewStaticText.Create(ModelPage);
  HwLabel.Parent := ModelPage.Surface;
  HwLabel.AutoSize := False;
  HwLabel.WordWrap := True;
  HwLabel.SetBounds(0, 0, W, ScaleY(30));

  ListH := Y - ScaleY(6) - ScaleY(34);
  if ListH < ScaleY(100) then ListH := ScaleY(100);
  ModelList := TNewCheckListBox.Create(ModelPage);
  ModelList.Parent := ModelPage.Surface;
  ModelList.SetBounds(0, ScaleY(34), W, ListH);
  ModelList.OnClickCheck := @ModelChoiceChanged;

  // An earlier install that started with Windows keeps doing so unless the
  // person unticks it here.
  Startup := ExpandConstant('{userstartup}');
  if FileExists(Startup + '\Agent Friday.lnk') or FileExists(Startup + '\FRIDAY Desktop.lnk') then
    WizardSelectTasks('autostart');
end;

// ---------------------------------------------------------------------------
//  What the person chose, for the ready page and for first-run.json
// ---------------------------------------------------------------------------

procedure FindOption(Id, Packing: String; var Found: Boolean; var O: TModelOption);
var
  I: Integer;
begin
  Found := False;
  for I := 0 to GetArrayLength(Opts) - 1 do
    if (Opts[I].Id = Id) and ((Packing = '') or (Opts[I].Packing = Packing)) then
    begin
      O := Opts[I];
      Found := True;
      Exit;
    end;
end;

procedure ResolveChoice();
var
  D, F: Integer;
  Found: Boolean;
  O: TModelOption;
begin
  ChosenCloud := True; ChosenConsent := False; ChosenDeepComp := False;
  ChosenFast := ''; ChosenFastPack := ''; ChosenDeep := ''; ChosenDeepPack := ''; ChosenDeepPick := '';
  ChosenTotal := 0; ChosenTier := TierId;

  if WizardSilent then
  begin
    // Command line only. Models are never downloaded unless asked for AND consented to.
    ChosenFast := ExpandConstant('{param:ModelFast|}');
    ChosenFastPack := ExpandConstant('{param:ModelFastPacking|}');
    ChosenDeep := ExpandConstant('{param:ModelDeep|}');
    ChosenDeepPack := ExpandConstant('{param:ModelDeepPacking|}');
    ChosenConsent := ExpandConstant('{param:ConsentDownload|0}') = '1';
    ChosenCloud := (ExpandConstant('{param:ModelsCloud|0}') = '1') or (ChosenFast = '') or (ChosenDeep = '') or (not ChosenConsent);
    if ChosenCloud then
    begin
      ChosenFast := ''; ChosenDeep := ''; ChosenConsent := False;
    end
    else if ChosenDeep = 'bonsai2:27b' then
    begin
      // The serving numbers for this computer come from the same probe.
      if LoadProbe() then
      begin
        FindOption(ChosenDeep, ChosenDeepPack, Found, O);
        if Found then
        begin
          ChosenDeepPick := O.PickJson;
          ChosenDeepComp := O.Companions;
          ChosenTier := TierId;
        end;
      end;
    end;
    Exit;
  end;

  if CloudCheck.Checked then Exit;
  D := SelectedOption(DeepItem, DeepOpt);
  F := SelectedOption(FastItem, FastOpt);
  if (D < 0) or (F < 0) or (not ConsentCheck.Checked) then Exit;
  ChosenCloud := False;
  ChosenConsent := True;
  ChosenFast := Opts[F].Id; ChosenFastPack := Opts[F].Packing;
  ChosenDeep := Opts[D].Id; ChosenDeepPack := Opts[D].Packing;
  ChosenDeepPick := Opts[D].PickJson;
  ChosenDeepComp := Opts[D].Companions;
  ChosenTotal := TotalBytesChosen();
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  S: String;
begin
  ResolveChoice();
  S := 'Installing {#ReleaseName} for this Windows user only.' + NewLine + NewLine;
  S := S + 'Location:' + NewLine + Space + ExpandConstant('{app}') + NewLine + NewLine;
  if PrevSequence > 0 then
  begin
    if PrevSequence = {#BuildSequence} then
      S := S + 'Reinstalling over the same release. Your data is kept.' + NewLine + NewLine
    else
      S := S + 'Updating Agent Friday ' + PrevVersion + ' in place.' + NewLine +
        Space + 'Setup first stops Agent Friday and copies your data to a backup folder, then checks it is unchanged afterwards.' + NewLine + NewLine;
  end;
  if ChosenCloud then
    S := S + 'AI models:' + NewLine + Space + 'A cloud model. Nothing is downloaded; you add a key in Settings.' + NewLine + NewLine
  else
    S := S + 'AI models (downloaded when Agent Friday first starts):' + NewLine +
      Space + 'Deep thinker: ' + ChosenDeep + NewLine + Space + 'Fast responder: ' + ChosenFast + NewLine +
      Space + 'Total: ' + GbText(ChosenTotal) + NewLine + NewLine;
  if MemoTasksInfo <> '' then S := S + MemoTasksInfo;
  Result := S;
end;

// ---------------------------------------------------------------------------
//  The install itself
// ---------------------------------------------------------------------------

procedure WriteFirstRun();
var
  J: String;
begin
  J := '{' + #13#10 +
    '  "schema": 1,' + #13#10 +
    '  "written_by": "installer",' + #13#10 +
    '  "release": "{#AppVersion}",' + #13#10 +
    '  "build_sequence": {#BuildSequence},' + #13#10 +
    '  "cloud": ' + BoolJson(ChosenCloud) + ',' + #13#10 +
    '  "consent_download": ' + BoolJson(ChosenConsent) + ',' + #13#10 +
    '  "total_bytes": ' + IntToStr(ChosenTotal) + ',' + #13#10 +
    '  "hardware": { "tier": "' + JsonEscape(ChosenTier) + '", "ram_mib": ' + IntToStr(FactRam) +
      ', "vram_mib": ' + IntToStr(FactVram) + ', "disk_free_mib": ' + IntToStr(FactDisk) + ' },' + #13#10 +
    '  "seats": {';
  if not ChosenCloud then
  begin
    J := J + #13#10 +
      '    "fast_responder": { "model_id": "' + JsonEscape(ChosenFast) + '", "packing": "' + JsonEscape(ChosenFastPack) + '" },' + #13#10 +
      '    "deep_thinker": { "model_id": "' + JsonEscape(ChosenDeep) + '", "packing": "' + JsonEscape(ChosenDeepPack) + '", ' +
      '"with_companions": ' + BoolJson(ChosenDeepComp);
    if ChosenDeepPick <> '' then J := J + ', "pick": ' + ChosenDeepPick;
    J := J + ' }' + #13#10 + '  ';
  end;
  J := J + '}' + #13#10 + '}' + #13#10;
  SaveStringToFile(ExpandConstant('{app}\first-run.json'), J, False);
  WriteSetupLog('Wrote first-run.json (cloud=' + BoolJson(ChosenCloud) + ', consent=' + BoolJson(ChosenConsent) + ').');
end;

function ShortcutPath(Kind: String): String;
begin
  if Kind = 'desktop' then
    Result := ExpandConstant('{userdesktop}\{#AppName}.lnk')
  else
    Result := ExpandConstant('{userprograms}\{#AppName}\{#AppName}.lnk');
end;

function VerifyShortcuts(): Boolean;
var
  D, S: Boolean;
begin
  D := FileExists(ShortcutPath('desktop'));
  S := FileExists(ShortcutPath('start'));
  WriteSetupLog('Desktop shortcut ' + ShortcutPath('desktop') + ' exists: ' + BoolJson(D));
  WriteSetupLog('Start menu shortcut ' + ShortcutPath('start') + ' exists: ' + BoolJson(S));
  Result := D and S;
end;

procedure EnsureShortcuts();
var
  Code: Integer;
  Script: String;
begin
  if VerifyShortcuts() then Exit;
  Script := ExpandConstant('{app}\tools\ensure-shortcuts.ps1');
  if not FileExists(Script) then
  begin
    WriteSetupLog('A shortcut is missing and the repair script is not installed.');
    Exit;
  end;
  WriteSetupLog('A shortcut is missing; recreating it through the Windows shell''s own folders.');
  Exec(PowerShellExe(), '-NoProfile -ExecutionPolicy Bypass -File "' + Script + '" -InstallRoot "' + ExpandConstant('{app}') + '"',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
  if not VerifyShortcuts() then
  begin
    ShortcutNote := 'Setup could not create every shortcut. Agent Friday is installed; start it from ' + ExpandConstant('{app}\Agent Friday.cmd') + '.';
    WriteSetupLog(ShortcutNote);
    SuppressibleMsgBox(ShortcutNote, mbInformation, MB_OK, IDOK);
  end;
end;

procedure RunEngine();
var
  SetupDir, Params, LogHint: String;
  Code: Integer;
  Started: Boolean;
begin
  SetupDir := ExpandConstant('{tmp}\af-setup');
  LogHint := ExpandConstant('{app}\logs');
  ForceDirectories(LogHint);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + SetupDir + '\install.ps1"' +
    ' -InstallRoot "' + ExpandConstant('{app}') + '" -Unattended -SkipOllama -InnoManaged' +
    ' -BuildSequence {#BuildSequence} -ReleaseName "{#ReleaseName}"';
  if ExpandConstant('{param:SkipMemory|0}') = '1' then Params := Params + ' -SkipMemory';
  if ExpandConstant('{param:SkipJudgment|0}') = '1' then Params := Params + ' -SkipJudgment';
  // Packages install offline from the wheels this program carries; this puts the index back.
  if ExpandConstant('{param:AllowNetwork|0}') = '1' then Params := Params + ' -AllowNetwork';

  WizardForm.StatusLabel.Caption := 'Installing Agent Friday. Most of this is setting up its parts, and it can take several minutes.';
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  WriteSetupLog('Running install.ps1: ' + Params);
  Started := Exec(PowerShellExe(), Params, SetupDir, SW_HIDE, ewWaitUntilTerminated, Code);
  WizardForm.ProgressGauge.Style := npbstNormal;
  if (not Started) or (Code <> 0) then
  begin
    WriteSetupLog('install.ps1 failed (started=' + BoolJson(Started) + ', exit ' + IntToStr(Code) + ').');
    RaiseException('Setup could not finish installing Agent Friday. Nothing of yours was changed. ' +
      'The details are in ' + LogHint + '.');
  end;
  WriteSetupLog('install.ps1 finished.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RunEngine();
    EnsureShortcuts();
    // The 5.x installer's own Apps entry is replaced by this program's.
    if RegKeyExists(HKCU, '{#LegacyUninstKey}') then
    begin
      RegDeleteKeyIncludingSubkeys(HKCU, '{#LegacyUninstKey}');
      WriteSetupLog('Removed the earlier installer''s entry from the Apps list.');
    end;
    ResolveChoice();
    WriteFirstRun();
  end;
end;

// ---------------------------------------------------------------------------
//  Uninstall: keep the person's data unless they say otherwise
// ---------------------------------------------------------------------------

function HostsHasFridayBlock(): Boolean;
var
  Text: AnsiString;
begin
  Result := False;
  if LoadStringFromFile(ExpandConstant('{sys}\drivers\etc\hosts'), Text) then
    Result := Pos('Agent Friday local address', String(Text)) > 0;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  RemoveEverything := False;
  if UninstallSilent then
  begin
    RemoveEverything := ExpandConstant('{param:REMOVEDATA|0}') = '1';
    Exit;
  end;
  // Default button is Yes = keep.
  if MsgBox('Agent Friday will be removed from this computer.' + #13#10#13#10 +
            'Keep your notes, conversations and settings, and the passphrase that unlocks them? ' +
            'If you install Agent Friday again she picks up where she left off.' + #13#10#13#10 +
            'Choose Yes to keep them (recommended). Choose No to delete them as well.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then
  begin
    if MsgBox('Delete your notes, conversations and settings, and the passphrase that unlocks them?' + #13#10#13#10 +
              'This cannot be undone.', mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      RemoveEverything := True;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Script, Params: String;
  Code: Integer;
begin
  if CurUninstallStep <> usUninstall then Exit;
  Script := ExpandConstant('{app}\tools\uninstall.ps1');
  if not FileExists(Script) then Exit;
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + Script + '" -InstallRoot "' + ExpandConstant('{app}') + '" -Unattended -InnoManaged';
  if RemoveEverything then Params := Params + ' -RemoveEverything';
  if (not UninstallSilent) and HostsHasFridayBlock() then
  begin
    if MsgBox('Agent Friday added a local address to this computer''s hosts file. Remove it now? Windows will ask for permission.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDYES then
      Params := Params + ' -AllowElevation';
  end;
  Exec(PowerShellExe(), Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code);
  Log('uninstall.ps1 exited ' + IntToStr(Code));
end;
