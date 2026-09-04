param(
    [ValidateSet("scheduler", "health")]
    [string]$Mode = "scheduler",
    [int]$Minutes = 5,
    [int]$Accounts = 100,
    [double]$IntervalSeconds = 2
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root "backend\.venv\Scripts\python.exe"

if ($Minutes -lt 1) { throw "Minutes must be at least 1" }

if ($Mode -eq "scheduler") {
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Backend virtual environment is missing. Run START.bat once first."
    }
    $Seconds = $Minutes * 60
    Write-Host "[soak] Scheduler mode: $Accounts accounts for $Minutes minute(s)"
    & $Python (Join-Path $Root "backend\soak_scheduler.py") --seconds $Seconds --accounts $Accounts
    if ($LASTEXITCODE -ne 0) { throw "Scheduler soak failed" }
    exit 0
}

$Uri = "http://127.0.0.1:8000/api/health"
$Deadline = (Get-Date).AddMinutes($Minutes)
$Checks = 0
$Failures = 0
$PeakWorkingSetMb = 0.0
Write-Host "[soak] Health mode: $Uri for $Minutes minute(s)"

while ((Get-Date) -lt $Deadline) {
    $Checks++
    try {
        $Health = Invoke-RestMethod -Uri $Uri -TimeoutSec 5
        if ($Health.backend -ne "ok" -or $Health.database -ne "ok" -or $Health.secret_store -ne "ok") {
            throw "Unhealthy response: $($Health | ConvertTo-Json -Compress)"
        }

        $Listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($Listener) {
            $Proc = Get-Process -Id $Listener.OwningProcess -ErrorAction SilentlyContinue
            if ($Proc) {
                $WorkingSetMb = [math]::Round($Proc.WorkingSet64 / 1MB, 1)
                if ($WorkingSetMb -gt $PeakWorkingSetMb) { $PeakWorkingSetMb = $WorkingSetMb }
            }
        }
        if (($Checks % 15) -eq 0) {
            Write-Host "[soak] checks=$Checks failures=$Failures peak_working_set_mb=$PeakWorkingSetMb"
        }
    } catch {
        $Failures++
        Write-Warning "[soak] check $Checks failed: $($_.Exception.Message)"
    }
    Start-Sleep -Milliseconds ([math]::Max(100, [int]($IntervalSeconds * 1000)))
}

Write-Host "[soak] completed checks=$Checks failures=$Failures peak_working_set_mb=$PeakWorkingSetMb"
if ($Failures -gt 0) {
    throw "Health soak recorded $Failures failed check(s)"
}
