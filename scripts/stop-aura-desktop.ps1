param([string]$DesktopPython)
$root = Split-Path -Parent $PSScriptRoot
if (-not $DesktopPython) { $DesktopPython = Join-Path $root ".runtime/desktop-venv/Scripts/python.exe" }
& $DesktopPython (Join-Path $root "desktop/shutdown_request.py")
exit $LASTEXITCODE
