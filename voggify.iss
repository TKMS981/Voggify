; Voggify のインストーラー定義（Inno Setup 6）
;
;   1. pyinstaller voggify.spec        -> dist\Voggify.exe
;   2. ISCC voggify.iss                -> dist\installer\Voggify-Setup-<version>.exe
;
; バージョンは dist\Voggify.exe のバージョンリソースから読む。
; その値は voggify.spec が voggify/__init__.py の __version__ から埋め込んでいるので、
; バージョンを上げるときに触るのは voggify/__init__.py の 1 行だけでよい。

#define AppName        "Voggify"
#define AppPublisher   "Voggify"
#define AppExeName     "Voggify.exe"
#define AppSourceExe   "dist\" + AppExeName

#ifnexist AppSourceExe
  #error dist\Voggify.exe がありません。先に `pyinstaller voggify.spec` を実行してください。
#endif

#define AppVersion GetStringFileInfo(AppSourceExe, "FileVersion")
#if AppVersion == ""
  #error dist\Voggify.exe からバージョンを読み取れませんでした。
#endif

; 設定ファイルの置き場所（アプリ側の voggify/config.py と一致させること）
#define ConfigDirName  "Voggify"
#define ConfigFileName "config.json"

[Setup]
; AppId は絶対に変えないこと。これが同じである限り、Inno Setup が
; 既存のインストールを見つけて上書き更新してくれる。
AppId={{8F3C1D42-6B7A-4E59-9C88-2A5D0E17B3F6}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher={#AppPublisher}
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}

; 管理者権限を要求しない。ユーザー単位でインストールする。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

OutputDir=dist\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

LicenseFile=LICENSE
SetupIconFile=
; アイコンを用意したら SetupIconFile=assets\voggify.ico のように指定する

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english";  MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "LICENSE";            DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller の onefile が展開に使う一時フォルダは %TEMP% なので触らない。
; 設定ファイル（{userappdata}\Voggify）はここに書かない。
; アンインストール時に消すかどうかは下の [Code] でユーザーに尋ねる。
Type: dirifempty; Name: "{app}"

[Code]

{ ---------------------------------------------------------------------------
  ffmpeg の検出
  アプリ側 voggify/ffmpeg_locator.py の探索先と揃えている。
  --------------------------------------------------------------------------- }

function FindInPath(const ExeName: String): Boolean;
begin
  { FileSearch はセミコロン区切りのフォルダ一覧を辿るので PATH をそのまま渡せる }
  Result := FileSearch(ExeName, GetEnv('PATH')) <> '';
end;

function FindInWingetPackages(const ExeName: String): Boolean;
var
  Base: String;
  FindRec: TFindRec;
  Inner: TFindRec;
  Candidate: String;
begin
  Result := False;
  Base := ExpandConstant('{localappdata}\Microsoft\WinGet\Packages');
  if not DirExists(Base) then
    Exit;

  if FindFirst(Base + '\*FFmpeg*', FindRec) then
  try
    repeat
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        { winget のポータブル配置は <パッケージ>\<展開先>\bin\ffmpeg.exe }
        Candidate := Base + '\' + FindRec.Name + '\bin\' + ExeName;
        if FileExists(Candidate) then
        begin
          Result := True;
          Exit;
        end;
        if FindFirst(Base + '\' + FindRec.Name + '\*', Inner) then
        try
          repeat
            if ((Inner.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0)
              and (Inner.Name <> '.') and (Inner.Name <> '..') then
            begin
              Candidate := Base + '\' + FindRec.Name + '\' + Inner.Name + '\bin\' + ExeName;
              if FileExists(Candidate) then
              begin
                Result := True;
                Exit;
              end;
            end;
          until not FindNext(Inner);
        finally
          FindClose(Inner);
        end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function FfmpegAvailable(): Boolean;
begin
  Result := (GetEnv('VOGGIFY_FFMPEG') <> '')
    or FindInPath('ffmpeg.exe')
    or FileExists('C:\ffmpeg\bin\ffmpeg.exe')
    or FileExists(ExpandConstant('{commonpf}\ffmpeg\bin\ffmpeg.exe'))
    or FileExists(ExpandConstant('{localappdata}\Microsoft\WinGet\Links\ffmpeg.exe'))
    or FileExists(ExpandConstant('{%USERPROFILE}\scoop\shims\ffmpeg.exe'))
    or FileExists('C:\ProgramData\chocolatey\bin\ffmpeg.exe')
    or FindInWingetPackages('ffmpeg.exe');
end;

{ ---------------------------------------------------------------------------
  完了画面での案内
  --------------------------------------------------------------------------- }

{ 行頭に # を書くと ISPP がプリプロセッサ指令と解釈してしまうので、
  改行はこの関数を通して組み立てる。 }
function NL(): String;
begin
  Result := Chr(13) + Chr(10);
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Note: String;
begin
  if CurPageID <> wpFinished then
    Exit;
  if FfmpegAvailable() then
    Exit;

  Note := NL() + NL()
    + '【ご注意】ffmpeg が見つかりませんでした。' + NL()
    + 'Voggify の変換には ffmpeg が必要です。次のコマンドでインストールできます:' + NL()
    + NL()
    + '    winget install Gyan.FFmpeg' + NL()
    + NL()
    + 'インストール後、Voggify の警告バーにある「再確認」を押すと認識されます。';

  WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + Note;
  WizardForm.FinishedLabel.AutoSize := False;
  WizardForm.FinishedLabel.Height := WizardForm.FinishedLabel.Parent.ClientHeight
    - WizardForm.FinishedLabel.Top;
end;

{ ---------------------------------------------------------------------------
  アンインストール時に設定を消すかどうか尋ねる。
  設定は %APPDATA%\Voggify にあり、インストール先の外に置かれている。
  そのため何もしなければ残る（= 再インストールで引き継がれる）。
  --------------------------------------------------------------------------- }

{ コマンドラインに指定があったか調べる（サイレント配布とテスト用） }
function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
  begin
    if CompareText(ParamStr(I), '/' + Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ConfigDir: String;
  ShouldDelete: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  ConfigDir := ExpandConstant('{userappdata}\{#ConfigDirName}');
  if not DirExists(ConfigDir) then
    Exit;

  if HasSwitch('DELETECONFIG') then
    { 尋ねずに消す（IT 部門による一括アンインストールなど） }
    ShouldDelete := True
  else if HasSwitch('KEEPCONFIG') then
    ShouldDelete := False
  else
    { 既定は「いいえ」。/SUPPRESSMSGBOXES 付きのサイレント実行でも残る }
    ShouldDelete := SuppressibleMsgBox(
      '設定ファイルも削除しますか?' + NL() + NL()
      + ConfigDir + NL() + NL()
      + '「いいえ」を選ぶと設定は残り、次に Voggify を入れ直したときに引き継がれます。',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;

  if ShouldDelete then
    DelTree(ConfigDir, True, True, True);
end;
