#define AppName "Agent Hub 统一代理"
#define AppVersion "0.3.0"
[Setup]
AppId={{7D9C731D-14F9-43DD-B34A-1952E6C02F0C}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\AgentHub
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=AgentHub-Setup
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\AgentHub.exe

[Files]
Source: "..\build\AgentHub\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\AgentHub.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\AgentHub.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Run]
Filename: "{app}\AgentHub.exe"; Description: "启动 Agent Hub"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v AgentHub /f"; Flags: runhidden
