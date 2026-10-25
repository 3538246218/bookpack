$ErrorActionPreference = 'Stop'
$appFile = Join-Path $PSScriptRoot 'app.py'
$pythonCommand = Get-Command pythonw.exe -ErrorAction SilentlyContinue
$pythonFile = if ($pythonCommand) { $pythonCommand.Source } else { $null }
if (-not $pythonFile) {
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
    if (Test-Path -LiteralPath $candidate) { $pythonFile = $candidate }
}
if (-not $pythonFile) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonFile = $pythonCommand.Source }
}
if (-not $pythonFile) { throw '未找到 Python，请先安装 Python 3.10 或更高版本。' }
Start-Process -FilePath $pythonFile -ArgumentList @(('"' + $appFile + '"'), '--open') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
