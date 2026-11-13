#define AppName "WorkBuddy 团队网关节点"
#define AppVersion "1.0.1"
[Setup]
AppId={{BEC33B5C-D1D8-4402-9A50-D6A7B9CF761F}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\WorkBuddyGatewayNode
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=WorkBuddyGatewayNode-Setup
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\WorkBuddyGatewayNode.exe

[Files]
Source: "..\build\WorkBuddyGatewayNode\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\WorkBuddyGatewayNode.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\WorkBuddyGatewayNode.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Run]
Filename: "{app}\WorkBuddyGatewayNode.exe"; Description: "启动 WorkBuddy 团队网关节点"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v AgentGatewayNode /f"; Flags: runhidden
