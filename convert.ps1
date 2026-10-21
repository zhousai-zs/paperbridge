param([string[]]$InputPaths, [ValidateSet('basic','standard','advanced')][string]$Tier='standard')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host 'Please run setup.ps1 first.'
    exit 1
}
if (-not $InputPaths) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'PaperBridge - Select PDF papers'
    $dialog.Filter = 'PDF papers (*.pdf)|*.pdf'
    $dialog.Multiselect = $true
    if ($dialog.ShowDialog() -ne 'OK') { exit 0 }
    $InputPaths = $dialog.FileNames
}
Write-Host 'PaperBridge: converting locally. The first run may download models.'
& $pythonExe -m paperbridge.cli convert @InputPaths --tier $Tier
$result = $LASTEXITCODE
Write-Host ('Output folder: ' + (Join-Path $PSScriptRoot 'outputs'))
Write-Host 'See document.md and report.json inside each result folder.'
exit $result
