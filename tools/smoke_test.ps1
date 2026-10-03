# Build the extension, install it into an ISOLATED user folder and run the headless smoke test.
# Run after every Blender update, before class. Your real Blender setup is not touched.
#   .\tools\smoke_test.ps1                    # Blender 5.2 LTS, no GPU job
#   .\tools\smoke_test.ps1 -Book              # also books a short job on the served workflow
#   .\tools\smoke_test.ps1 -App mixar         # Mixar (uninstalled on 2026-10-03, kept for later)
#   .\tools\smoke_test.ps1 -Ui [-Book]       # VISIBLE window: presses every ComfyQ button on a copy of
#                                            # -Scene (default Desktop\testV2V_clean.mixar); report in live_check\ui
#   .\tools\smoke_test.ps1 -Sweep [-SweepArgs "--only=a,b --no-calibrate"]   # P1.5: calibrate + run every
#                                            # SERVED workflow on a copy of -Scene (GPU!) -> live_check\sweep
param(
    [ValidateSet("mixar", "blender")] [string] $App = "blender",
    [switch] $Book,
    [switch] $Ui,
    [switch] $Sweep,
    [string] $SweepArgs = "",
    [string] $Scene = (Join-Path $env:USERPROFILE "Desktop\testV2V_clean.mixar")
)
# Not "Stop": Mixar writes harmless warnings to stderr, which PowerShell 5.1 turns into errors.
# Exit codes are checked explicitly instead.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$exe = @{
    mixar   = "C:\Program Files\Mixar\mixar.exe"
    blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
}[$App]
if (-not (Test-Path $exe)) { Write-Error "$App not found at $exe"; exit 1 }

$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
Get-ChildItem $dist -Filter "comfyq_bridge-*.zip" | Remove-Item -Force
& $exe --command extension build --source-dir (Join-Path $root "extension\comfyq_bridge") --output-dir $dist
$zip = Get-ChildItem $dist -Filter "comfyq_bridge-*.zip" | Select-Object -First 1
if (-not $zip) { Write-Error "build produced no zip"; exit 1 }

$sandbox = Join-Path $env:TEMP "comfyq_bridge_smoke_$App"
if (Test-Path $sandbox) { Remove-Item -Recurse -Force $sandbox }
New-Item -ItemType Directory -Force $sandbox | Out-Null
# Mixar renames Blender's variables (MIXAR_USER_RESOURCES); set both so neither host touches the real profile.
$env:BLENDER_USER_RESOURCES = $sandbox
$env:MIXAR_USER_RESOURCES = $sandbox

$real = Join-Path $env:APPDATA "Mixar\Mixar\5.2\extensions\user_default\comfyq_bridge"
$realBefore = if (Test-Path $real) { (Get-Item $real).LastWriteTime } else { $null }
& $exe -b --factory-startup --online-mode --command extension install-file -r user_default -e $zip.FullName 2>&1 |
    Where-Object { "$_" -notmatch "SyntaxWarning|^\s*$" } | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Error "install failed ($LASTEXITCODE)"; exit 1 }
$realAfter = if (Test-Path $real) { (Get-Item $real).LastWriteTime } else { $null }
if ($realBefore -ne $realAfter) { Write-Error "Isolation failed: the real Mixar profile was modified"; exit 1 }
if (-not (Test-Path (Join-Path $sandbox "extensions\user_default\comfyq_bridge"))) {
    Write-Error "Isolation failed: nothing was installed in the sandbox"; exit 1
}
if ($Ui) {
    $out = Join-Path $root "live_check\ui"
    if (Test-Path $out) { Remove-Item -Recurse -Force $out }
    New-Item -ItemType Directory -Force $out | Out-Null
    $uiArgs = @("--online-mode", "--python", (Join-Path $root "tests\blender\ui_check.py"), "--", $Scene, $out)
    if ($Book) { $uiArgs += "--book" }
    Start-Process -FilePath $exe -ArgumentList $uiArgs -Wait -NoNewWindow `
        -RedirectStandardOutput (Join-Path $out "console.log") -RedirectStandardError (Join-Path $out "stderr.log")
    Remove-Item Env:\BLENDER_USER_RESOURCES, Env:\MIXAR_USER_RESOURCES
    Select-String -Path (Join-Path $out "console.log") -Pattern "^  (ok|FAIL) |^== " | ForEach-Object { $_.Line }
    $report = Get-Content (Join-Path $out "report.json") -Raw | ConvertFrom-Json
    if ($report.failed.Count -or $report.draw_errors.Count -or -not $report.finished) { exit 1 }
    exit 0
}
if ($Sweep) {
    $out = Join-Path $root "live_check\sweep"
    New-Item -ItemType Directory -Force $out | Out-Null
    $copy = Join-Path $sandbox ("scene" + [IO.Path]::GetExtension($Scene))
    Copy-Item $Scene $copy
    $sw = @("-b", "--online-mode", "--python", (Join-Path $root "tests\blender\sweep.py"), "--", $copy, $out)
    if ($SweepArgs) { $sw += $SweepArgs.Split(" ") }
    & $exe @sw 2>&1 | Where-Object { "$_" -match "^\[sweep\]|Traceback|Error" } | Out-Host
    Remove-Item Env:\BLENDER_USER_RESOURCES, Env:\MIXAR_USER_RESOURCES
    & (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $root "tools\sweep_matrix.py")
    exit 0
}
$smokeArgs = @("-b", "--online-mode", "--python-exit-code", "1", "--python", (Join-Path $root "tests\blender\smoke.py"), "--",
               "--zip", $zip.FullName)
if ($Book) { $smokeArgs += "--book" }
& $exe @smokeArgs
$code = $LASTEXITCODE
Remove-Item Env:\BLENDER_USER_RESOURCES, Env:\MIXAR_USER_RESOURCES
exit $code
