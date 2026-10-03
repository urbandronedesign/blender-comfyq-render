# Agent round trip (P1.4 acceptance): a VISIBLE sandbox Blender with the MCP server on, driven by
# Claude Code headless through tools/blender_mcp.py. Books ONE short GPU job. Your real Blender profile
# and your real %APPDATA%\comfyq-bridge are not touched (sandboxed config, copy of the scene).
#   .\tools\agent_roundtrip.ps1                       # after .\tools\smoke_test.ps1 (which builds dist\*.zip)
#   .\tools\agent_roundtrip.ps1 -Prompt my_prompt.md  # another task for the agent
param(
    [string] $Scene = (Join-Path $env:USERPROFILE "Desktop\testV2V_clean.mixar"),
    [string] $Prompt = (Join-Path $PSScriptRoot "agent_round_trip.md"),
    [string] $Claude = "",
    [int] $MaxTurns = 40
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$exe = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
if (-not $Claude) {
    $Claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
    if (-not $Claude) {
        $Claude = Get-ChildItem "$env:USERPROFILE\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe" |
            Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
    }
}
if (-not $Claude) { Write-Error "Claude Code CLI not found (pass -Claude <path>)"; exit 1 }
$zip = Get-ChildItem (Join-Path $root "dist") -Filter "comfyq_bridge-*.zip" | Select-Object -First 1
if (-not $zip) { Write-Error "no dist\comfyq_bridge-*.zip: run .\tools\smoke_test.ps1 first"; exit 1 }

$sandbox = Join-Path $env:TEMP "comfyq_bridge_agent"
if (Test-Path $sandbox) { Remove-Item -Recurse -Force $sandbox }
$cfg = Join-Path $sandbox "config"
New-Item -ItemType Directory -Force $cfg | Out-Null
$userCfg = Join-Path $env:APPDATA "comfyq-bridge\profiles.toml"
if (Test-Path $userCfg) { Copy-Item $userCfg $cfg }          # same server address as the user
$env:BLENDER_USER_RESOURCES = $sandbox
$env:COMFYQ_BRIDGE_CONFIG = Join-Path $cfg "profiles.toml"
& $exe -b --factory-startup --online-mode --command extension install-file -r user_default -e $zip.FullName 2>&1 |
    Select-Object -Last 1 | Out-Host
$copy = Join-Path $sandbox ("scene" + [IO.Path]::GetExtension($Scene))
Copy-Item $Scene $copy
$out = Join-Path $root "live_check\agent"
New-Item -ItemType Directory -Force $out | Out-Null
$ready = Join-Path $sandbox "ready.txt"
$proc = Start-Process -FilePath $exe -PassThru -NoNewWindow -ArgumentList @(
    "--online-mode", "--python", (Join-Path $PSScriptRoot "mcp_session.py"), "--", $copy, $ready) `
    -RedirectStandardOutput (Join-Path $out "blender.log") -RedirectStandardError (Join-Path $out "blender.err")
$t0 = Get-Date
while (-not (Test-Path $ready) -and ((Get-Date) - $t0).TotalSeconds -lt 120 -and -not $proc.HasExited) { Start-Sleep 1 }
if (-not (Test-Path $ready)) { Write-Error "Blender did not get ready (see live_check\agent\blender.log)"; Stop-Process $proc -ErrorAction SilentlyContinue; exit 1 }
"MCP server: " + (Get-Content $ready)

$mcp = Join-Path $sandbox "mcp_config.json"
@{ mcpServers = @{ "blender-comfyq" = @{ command = (Get-Command python).Source
    args = @((Join-Path $PSScriptRoot "blender_mcp.py")); env = @{ COMFYQ_BRIDGE_CONFIG = $env:COMFYQ_BRIDGE_CONFIG } } } } |
    ConvertTo-Json -Depth 5 | Set-Content -Encoding ascii $mcp
$task = Get-Content $Prompt -Raw -Encoding utf8
$started = Get-Date
# the task goes through stdin: PowerShell 5.1 does not escape quotes inside native arguments (it cut it)
$task | & $Claude -p --mcp-config $mcp --strict-mcp-config --allowedTools "mcp__blender-comfyq" `
    --max-turns $MaxTurns --output-format json 2>&1 | Set-Content -Encoding utf8 (Join-Path $out "claude.json")
$secs = [int]((Get-Date) - $started).TotalSeconds
Stop-Process $proc -ErrorAction SilentlyContinue
Remove-Item Env:\BLENDER_USER_RESOURCES, Env:\COMFYQ_BRIDGE_CONFIG
$res = Get-Content (Join-Path $out "claude.json") -Raw | ConvertFrom-Json
"agent finished in $secs s, turns $($res.num_turns), error $($res.is_error)"
$res.result
if ($res.is_error) { exit 1 }
exit 0
