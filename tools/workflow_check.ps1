# Run one ComfyQ workflow on the GPU without Blender (see workflow_check.py). Switches ComfyQ's models.
#   .\tools\workflow_check.ps1 <workflow id> <out folder> [key=value | key=@file ...] [next key=value ...]  (not "--": PowerShell swallows it)
$root = Split-Path -Parent $PSScriptRoot
$ext = Join-Path $root "extension\comfyq_bridge"
$python = @("5.3", "5.2") | ForEach-Object { "C:\Program Files\Blender Foundation\Blender $_\$_\python\bin\python.exe" } |
    Where-Object { Test-Path $_ } | Select-Object -First 1
$wheels = (Get-ChildItem (Join-Path $ext "wheels\*.whl") | ForEach-Object FullName) -join ";"
$env:PYTHONPATH = "$ext;$wheels;$PSScriptRoot"
$env:PYTHONUTF8 = "1"
& $python (Join-Path $PSScriptRoot "workflow_check.py") @args
exit $LASTEXITCODE
