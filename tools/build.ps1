$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$taskPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements-dev.txt first.' }
& $taskPython tools\build_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon render failed.' }
& $taskPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $taskPython -m PyInstaller --noconfirm tools\mapdln.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Host 'Built dist\mapdln.exe. Double-click launches the GUI without a Python installation.'
