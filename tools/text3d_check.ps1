# GPU check of the Text → 3D class preset without Blender (see text3d_check.py). Switches ComfyQ's models.
#   .\tools\text3d_check.ps1 [out folder]
$root = Split-Path -Parent $PSScriptRoot
$ext = Join-Path $root "extension\comfyq_bridge"
$python = @("5.3", "5.2") | ForEach-Object { "C:\Program Files\Blender Foundation\Blender $_\$_\python\bin\python.exe" } |
    Where-Object { Test-Path $_ } | Select-Object -First 1
$wheels = (Get-ChildItem (Join-Path $ext "wheels\*.whl") | ForEach-Object FullName) -join ";"
$env:PYTHONPATH = "$ext;$wheels;$PSScriptRoot"
$env:PYTHONUTF8 = "1"
& $python (Join-Path $PSScriptRoot "text3d_check.py") @args
exit $LASTEXITCODE
