$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.build-venv\Scripts\python.exe'
if (-not (Test-Path $python)) { py -3.11 -m venv (Join-Path $repo '.build-venv') }
& $python -m pip install -r (Join-Path $repo 'desktop\requirements.txt') 'pyinstaller>=6,<7'
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
$upstream = Join-Path $repo 'vendor\upstream.zip'
if (-not (Test-Path $upstream)) {
  Invoke-WebRequest 'https://github.com/Ianzhyh/workbuddy-to-dsh/archive/2f3c556d38896164bf138627b484662aa6f4d345.zip' -OutFile $upstream
}
$expectedUpstream = 'D0B288F413A1524225E3E793EB6E4E9BF38204C0BCC5D332D4220056AA0B20AA'
if ((Get-FileHash $upstream -Algorithm SHA256).Hash -ne $expectedUpstream) { throw 'Pinned upstream source hash mismatch' }
$node = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_*" -Filter node.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $node) { $node = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1 }
if (-not $node) { throw 'Install official Node.js LTS before building' }
$runtime = Join-Path $repo 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Copy-Item $node (Join-Path $runtime 'node.exe') -Force
Copy-Item (Join-Path (Split-Path $node) 'LICENSE') (Join-Path $runtime 'NODE-LICENSE.txt') -Force
Push-Location $repo
try {
  & $python -m PyInstaller --noconfirm --clean --onedir --windowed --name WorkBuddyGatewayNode --distpath build --workpath .build-work --specpath .build-work --add-data "$upstream;vendor" --add-data "$(Join-Path $repo 'vendor\UPSTREAM-LICENSE.txt');vendor" --add-data "$(Join-Path $runtime 'node.exe');runtime" --add-data "$(Join-Path $runtime 'NODE-LICENSE.txt');runtime" desktop\app.py
  if ($LASTEXITCODE) { throw 'PyInstaller failed' }
  $iscc = Get-ChildItem "$env:LOCALAPPDATA\Programs\Inno Setup*","$env:ProgramFiles\Inno Setup*","${env:ProgramFiles(x86)}\Inno Setup*" -Filter ISCC.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
  if (-not $iscc) { throw 'Install Inno Setup 6 before building' }
  & $iscc (Join-Path $repo 'packaging\WorkBuddyNode.iss')
  if ($LASTEXITCODE) { throw 'Inno Setup failed' }
} finally { Pop-Location }
