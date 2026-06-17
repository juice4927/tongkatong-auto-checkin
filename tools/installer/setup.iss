; 通卡通 Inno Setup 安装脚本
; 用法: ISCC.exe setup.iss /DAppVersion=2.2.13
; 或 build.py 自动调用（通过 run_innosetup）
;
; 关键设计：
; - 安装到 %LOCALAPPDATA%\通卡通（用户可写，无需管理员权限）
; - PrivilegesRequired=lowest → 不弹 UAC
; - 配置文件随程序保留，卸载时可选删除

#define MyAppName "通卡通"
#define MyAppPublisher "淙宝"
#define MyAppURL "https://github.com/juice4927/tongkatong-auto-checkin"

#ifndef AppVersion
  #define AppVersion "2.2.13"
#endif

#define MyAppExeName "通卡通_v" + AppVersion + ".exe"
#define MyAppDir "通卡通"

[Setup]
AppId={{8B2E5C12-7B3A-4F8E-9D1A-5C3F6E8B2A1D}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; ── 安装路径：用户可写目录，避免 Program Files 的 UAC 问题 ──
DefaultDirName={localappdata}\{#MyAppDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; ── 安全与兼容 ──
PrivilegesRequired=lowest             ; 不需要管理员权限
OutputBaseFilename=通卡通_v{#AppVersion}_安装包
OutputDir=.
Compression=lzma2/ultra64
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} v{#AppVersion}

; ── 安装包元数据 ──
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 自动打卡工具
ShowLanguageDialog=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 主程序文件
Source: "..\..\dist\releases\v{#AppVersion}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 配置文件目录
Source: "..\..\config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理配置文件（可选），注释掉以保留用户配置
; Type: filesandordirs; Name: "{app}\config"
; Type: filesandordirs; Name: "{app}\logs"
; Type: filesandordirs; Name: "{app}\screenshots"
