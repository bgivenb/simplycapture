$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE) { throw 'Virtual environment creation failed' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller==6.22.3
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& .\.venv\Scripts\python.exe branding.py
if ($LASTEXITCODE) { throw 'Icon generation failed' }
& .\.venv\Scripts\python.exe collect_licenses.py
if ($LASTEXITCODE) { throw 'License collection failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name SimplyCapture --icon icon.ico --version-file version_info.txt --add-data 'icon.png;.' --add-data 'icon_recording.png;.' --add-data 'icon.ico;.' --add-data 'LICENSE;.' --add-data 'THIRD_PARTY_NOTICES.md;.' --add-data 'build\third_party_licenses;third_party_licenses' --collect-all imageio_ffmpeg simplycapture.py
if ($LASTEXITCODE) { throw 'Build failed' }
Write-Host "Built $PSScriptRoot\dist\SimplyCapture.exe"
$captureCompiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
if (Test-Path $captureCompiler) {
    & $captureCompiler installer.iss
    if ($LASTEXITCODE) { throw 'Installer build failed' }
    Write-Host "Built $PSScriptRoot\dist\SimplyCapture-2.0.0-Setup.exe"
} else {
    Write-Host 'Portable app built. Install Inno Setup 6 to also build the installer.'
}
