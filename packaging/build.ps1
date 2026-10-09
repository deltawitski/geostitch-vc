param([switch]$OneFile)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -e ".[build]"

if ($OneFile) {
    # Single self-contained dist/GeoStitch.exe; no installer.
    python -m PyInstaller packaging/geostitch-onefile.spec --noconfirm --distpath dist --workpath build/pyinstaller-onefile
    return
}

python -m PyInstaller packaging/geostitch.spec --noconfirm --distpath dist --workpath build/pyinstaller

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $default = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $default) { $iscc = $default } else { throw "Inno Setup (iscc) not found." }
}
& $iscc packaging/installer.iss
