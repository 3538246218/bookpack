$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Write-Host '在本机浏览器打开 http://127.0.0.1:44082/ 查看交互原型。按 Ctrl+C 结束预览。'
python -X utf8 (Join-Path $PSScriptRoot 'tools\serve.py')
