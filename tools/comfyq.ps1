# Run the bridge's ComfyQ client CLI with Blender's bundled Python.
#   .\tools\comfyq.ps1 doctor
#   .\tools\comfyq.ps1 --user zaki submit image_qwen_image_2_1_t2i --prompt "a red fox" --fetch out
# Plain $args (no param block): an advanced script would swallow -p as -PipelineVariable.
$Rest = $args
$root = Split-Path -Parent $PSScriptRoot
$ext = Join-Path $root "extension\comfyq_bridge"
$candidates = @(@(
    $env:COMFYQ_BRIDGE_PYTHON,
    "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe"
) | Where-Object { $_ -and (Test-Path $_) })   # outer @(): one match must stay a list, not a string
if (-not $candidates) { Write-Error "No Blender Python found; set COMFYQ_BRIDGE_PYTHON"; exit 1 }

$wheels = (Get-ChildItem (Join-Path $ext "wheels\*.whl") | ForEach-Object FullName) -join ";"
$env:PYTHONPATH = "$ext;$wheels"
& $candidates[0] -m client @Rest
exit $LASTEXITCODE
