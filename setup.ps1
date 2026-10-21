param([string]$Python, [switch]$SkipModels)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Python) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { $Python = $bundled }
    else {
        $Python = (& py -3.12 -c 'import sys; print(sys.executable)' 2>$null)
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, or pass -Python with its full path.' }
    }
}
& $Python -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,15), "Python 3.12-3.14 required"'
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python runtime.' }
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv --without-pip .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
}
$pipCache = Join-Path $PSScriptRoot '.cache\pip'
& $Python -m pip --python $venvPython install --cache-dir $pipCache --index-url https://pypi.org/simple -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Locked dependencies could not be installed.' }
& $Python -m pip --python $venvPython install --cache-dir $pipCache --no-deps --no-build-isolation -e .
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not $SkipModels) {
    & $venvPython -c "from paperbridge.environment import configure; configure(); import subprocess; raise SystemExit(subprocess.call(['.venv/Scripts/mineru-kit.exe','models','download','--tier','standard','--source','modelscope']))"
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Re-run setup to resume.' }
}
& $venvPython -m paperbridge.cli doctor --models
