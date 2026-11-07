$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $runtimePython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    $installedPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $runtimePython) { $bootstrapPython = $runtimePython }
    elseif ($installedPython) { $bootstrapPython = $installedPython.Source }
    else { throw 'Install Python 3.11 or newer first.' }
    & $bootstrapPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
& $projectPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath 'instance/market.sqlite3')) {
    & $projectPython -m campus_market.cli --data-dir instance demo
    if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed.' }
}
Write-Host 'Open http://127.0.0.1:5053 in your browser. Press Ctrl+C here to stop.'
& $projectPython -m campus_market.cli --data-dir instance run --port 5053
