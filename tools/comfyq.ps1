# Run the bridge's ComfyQ client CLI with Mixar's (or Blender's) bundled Python.
#   .\tools\comfyq.ps1 doctor
#   .\tools\comfyq.ps1 --user zaki submit image_qwen_image_2_1_t2i --prompt "a red fox" --fetch out
# Plain $args (no param block): an advanced script would swallow -p as -PipelineVariable.
$Rest = $args
$root = Split-Path -Parent $PSScriptRoot
$ext = Join-Path $root "extension\comfyq_bridge"
$candidates = @(
    $env:COMFYQ_BRIDGE_PYTHON,
    "C:\Program Files\Mixar\5.2\python\bin\python.exe",
    "C:\Program Files\Blender Foundation\Blender 5.1\5.1\python\bin\python.exe"
) | Where-Object { $_ -and (Test-Path $_) }
if (-not $candidates) { Write-Error "No Mixar/Blender Python found; set COMFYQ_BRIDGE_PYTHON"; exit 1 }

$wheels = (Get-ChildItem (Join-Path $ext "wheels\*.whl") | ForEach-Object FullName) -join ";"
$env:PYTHONPATH = "$ext;$wheels"
& $candidates[0] -m client @Rest
exit $LASTEXITCODE
