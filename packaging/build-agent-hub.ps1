$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.build-venv\Scripts\python.exe'
if (-not (Test-Path $python)) { py -3.11 -m venv (Join-Path $repo '.build-venv') }
if (-not (Test-Path $python)) { throw 'Python 3.11 build environment unavailable' }
& $python -m pip install -r (Join-Path $repo 'desktop\requirements.txt') 'pyinstaller>=6,<7'
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
$upstream = Join-Path $repo 'vendor\upstream.zip'
$expected = 'D0B288F413A1524225E3E793EB6E4E9BF38204C0BCC5D332D4220056AA0B20AA'
if (-not (Test-Path $upstream)) {
  Invoke-WebRequest 'https://github.com/Ianzhyh/workbuddy-to-dsh/archive/2f3c556d38896164bf138627b484662aa6f4d345.zip' -OutFile $upstream
}
if ((Get-FileHash $upstream -Algorithm SHA256).Hash -ne $expected) { throw 'WorkBuddy source hash mismatch' }
$raccoon = Join-Path $repo 'vendor\raccoon2api'
$raccoonArchive = Join-Path $repo 'vendor\raccoon2api-source.zip'
$raccoonHash = 'B8C1960D24060EE2C611362CC205142DFA0C2AA8277687D0D1740B6ACE2C9637'
if ((Get-FileHash $raccoonArchive -Algorithm SHA256).Hash -ne $raccoonHash) { throw 'Raccoon source hash mismatch' }
if (-not (Test-Path (Join-Path $raccoon 'main.py'))) {
  New-Item -ItemType Directory -Path $raccoon -Force | Out-Null
  Expand-Archive -LiteralPath $raccoonArchive -DestinationPath $raccoon -Force
}
if (-not (Test-Path (Join-Path $raccoon 'LICENSE'))) { throw 'Raccoon license missing' }
$node = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_*" -Filter node.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $node) { $node = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1 }
if (-not $node) { throw 'Install official Node.js LTS before building' }
$nodeLicense = Join-Path (Split-Path $node) 'LICENSE'
if (-not (Test-Path $nodeLicense)) { throw 'Node.js LICENSE not found beside node.exe' }
$runtime = Join-Path $repo 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Copy-Item -LiteralPath $node -Destination (Join-Path $runtime 'node.exe') -Force
Copy-Item -LiteralPath $nodeLicense -Destination (Join-Path $runtime 'NODE-LICENSE.txt') -Force
Push-Location $repo
try {
  & $python -m PyInstaller --noconfirm --clean --onedir --windowed --name AgentHub `
    --distpath build --workpath .build-work --specpath .build-work `
    --collect-all uvicorn `
    --add-data "$upstream;vendor" `
    --add-data "$(Join-Path $repo 'vendor\UPSTREAM-LICENSE.txt');vendor" `
    --add-data "$(Join-Path $raccoon 'main.py');vendor/raccoon2api" `
    --add-data "$(Join-Path $raccoon 'LICENSE');vendor/raccoon2api" `
    --add-data "$(Join-Path $repo 'runtime\node.exe');runtime" `
    --add-data "$(Join-Path $repo 'runtime\NODE-LICENSE.txt');runtime" `
    desktop\aggregate_gui.py
  if ($LASTEXITCODE) { throw 'PyInstaller failed' }
  $icu = Join-Path $repo 'build\AgentHub\_internal\icuuc.dll'
  if (Test-Path $icu) { Remove-Item -LiteralPath $icu -Force }
  $iscc = Get-ChildItem "$env:LOCALAPPDATA\Programs\Inno Setup*","$env:ProgramFiles\Inno Setup*","${env:ProgramFiles(x86)}\Inno Setup*" -Filter ISCC.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
  if (-not $iscc) { throw 'Inno Setup 6 not found' }
  & $iscc (Join-Path $repo 'packaging\AgentHub.iss')
  if ($LASTEXITCODE) { throw 'Inno Setup failed' }
} finally { Pop-Location }
