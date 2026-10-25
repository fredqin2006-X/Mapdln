param(
    [string]$ExePath = 'dist\mapdln.exe',
    [double]$MaxSizeMiB = 220
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    & .\.venv\Scripts\python.exe tools\verify_build.py $ExePath --max-mib $MaxSizeMiB
    if ($LASTEXITCODE -ne 0) { throw 'Frozen build verification failed.' }
} finally {
    Pop-Location
}
