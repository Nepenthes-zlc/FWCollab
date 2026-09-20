param(
    [string]$BaseUrl = "http://127.0.0.1:23333/api/openai",
    [int]$PollSeconds = 30
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runRoot = Join-Path $repoRoot "artifacts\runs\leaderboard_v1"
$logPath = Join-Path $runRoot "_logs\closed4_watchdog.log"
$models = @("gpt-5.5", "gpt-5-mini", "gemini-3.7-flash", "gemini-3.5-flash")
$terminal = @{}

function Write-WatchLog([string]$Message) {
    $line = "$((Get-Date).ToString('o')) $Message"
    Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
}

function Get-ReplicateState([string]$ModelId, [int]$Replicate) {
    $path = Join-Path $runRoot "$ModelId\rep$Replicate\run_state.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
}

Write-WatchLog "watchdog started; recovery is restricted to incomplete infrastructure-error keys"
while ($terminal.Count -lt $models.Count) {
    foreach ($model in $models) {
        if ($terminal.ContainsKey($model)) { continue }

        $allComplete = $true
        $hardFailure = $false
        $hasInfrastructureError = $false
        foreach ($replicate in 1, 2, 3) {
            $state = Get-ReplicateState $model $replicate
            if ($null -eq $state -or [int]$state.status_counts.completed -ne 52) { $allComplete = $false }
            if ($null -ne $state) {
                if ([int]$state.status_counts.runner_error -gt 0 -or [int]$state.status_counts.interface_unavailable -gt 0) {
                    $hardFailure = $true
                }
                if ([int]$state.status_counts.infrastructure_error -gt 0) {
                    $hasInfrastructureError = $true
                }
            }
        }
        if ($allComplete) {
            $terminal[$model] = "COMPLETE"
            Write-WatchLog "$model complete: 156/156"
            continue
        }
        if ($hardFailure) {
            $terminal[$model] = "HARD_FAILURE"
            Write-WatchLog "$model stopped: runner/interface failure requires inspection"
            continue
        }

        $escaped = [regex]::Escape($model)
        $parentAlive = @(Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -match 'run_closed4_model_replicates\.ps1' -and $_.CommandLine -match $escaped
        }).Count -gt 0
        if (-not $parentAlive -and $hasInfrastructureError) {
            $stdout = Join-Path $runRoot "_logs\$model.stdout.log"
            $stderr = Join-Path $runRoot "_logs\$model.stderr.log"
            $args = @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                (Join-Path $repoRoot 'scripts\run_closed4_model_replicates.ps1'),
                '-ModelId', $model, '-BaseUrl', $BaseUrl
            )
            $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $args `
                -WorkingDirectory $repoRoot -RedirectStandardOutput $stdout `
                -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
            Write-WatchLog "$model recovery wrapper started pid=$($process.Id)"
        } elseif (-not $parentAlive) {
            $terminal[$model] = "UNEXPLAINED_STOP"
            Write-WatchLog "$model stopped without a recorded infrastructure error; not restarted"
        }
    }
    if ($terminal.Count -lt $models.Count) { Start-Sleep -Seconds $PollSeconds }
}
Write-WatchLog "watchdog terminal: $($terminal | ConvertTo-Json -Compress)"
exit 0
