param(
    [Parameter(Mandatory = $true)]
    [string]$ModelId,
    [string]$BaseUrl = "http://127.0.0.1:23333/api/openai",
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

foreach ($replicate in 1, 2, 3) {
    & $PythonExecutable scripts/run_standard52_leaderboard.py `
        --model-id $ModelId `
        --replicate $replicate `
        --base-url $BaseUrl
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$ModelId replicate $replicate failed integrity; later replicates were not started."
        exit $LASTEXITCODE
    }
}

exit 0
