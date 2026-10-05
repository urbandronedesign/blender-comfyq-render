# GPU check of the Text → 3D class preset without Blender (see text3d_check.py). Switches ComfyQ's models.
#   .\tools\text3d_check.ps1 [out folder]
$root = Split-Path -Parent $PSScriptRoot
$ext = Join-Path $root "extension\comfyq_bridge"
$python = "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe"
$wheels = (Get-ChildItem (Join-Path $ext "wheels\*.whl") | ForEach-Object FullName) -join ";"
$env:PYTHONPATH = "$ext;$wheels;$PSScriptRoot"
$env:PYTHONUTF8 = "1"
& $python (Join-Path $PSScriptRoot "text3d_check.py") @args
exit $LASTEXITCODE
