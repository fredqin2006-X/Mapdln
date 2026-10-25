$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$taskPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements-dev.txt first.' }
& $taskPython tools\build_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon render failed.' }
& $taskPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $taskPython -m PyInstaller --clean --noconfirm tools\mapdln.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
& $PSScriptRoot\verify_build.ps1
if ($LASTEXITCODE -ne 0) { throw 'Build verification failed.' }

# Update the actual double-click entry point only after validating the new EXE.
$taskBuilt = Join-Path (Get-Location) 'dist\mapdln.exe'
$taskEntry = Join-Path (Get-Location) 'mapdln.exe'
$taskBuiltHash = (Get-FileHash -LiteralPath $taskBuilt -Algorithm SHA256).Hash
$taskOldSize = 0
if (Test-Path -LiteralPath $taskEntry) {
    $taskOldSize = (Get-Item -LiteralPath $taskEntry).Length
    $taskOldHash = (Get-FileHash -LiteralPath $taskEntry -Algorithm SHA256).Hash
    if ($taskOldHash -ne $taskBuiltHash) {
        $taskBackupDir = Join-Path (Get-Location) 'test-output\build-backup'
        New-Item -ItemType Directory -Path $taskBackupDir -Force | Out-Null
        $taskBackup = Join-Path $taskBackupDir ('mapdln-' + $taskOldHash.Substring(0,12).ToLowerInvariant() + '.exe')
        if (-not (Test-Path -LiteralPath $taskBackup)) {
            Copy-Item -LiteralPath $taskEntry -Destination $taskBackup
        }
    }
}
Copy-Item -LiteralPath $taskBuilt -Destination $taskEntry -Force
if ((Get-FileHash -LiteralPath $taskEntry -Algorithm SHA256).Hash -ne $taskBuiltHash) {
    throw 'The root EXE does not match dist\mapdln.exe.'
}
$taskSize = (Get-Item -LiteralPath $taskEntry).Length
Write-Host ('Updated mapdln.exe: {0:N2} MiB; SHA256 {1}' -f ($taskSize / 1MB), $taskBuiltHash.ToLowerInvariant())
if ($taskOldSize -gt $taskSize) {
    Write-Host ('Saved {0:N2} MiB ({1:P1}); previous EXE backed up in test-output\build-backup.' -f (($taskOldSize - $taskSize) / 1MB), (1 - $taskSize / $taskOldSize))
}
