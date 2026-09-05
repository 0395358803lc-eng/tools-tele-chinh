param(
    [Parameter(Mandatory = $true)]
    [string]$ZipPath,
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "Release archive not found: $ZipPath"
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $Root "backend\.venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python runtime not found: $PythonPath"
}

$TempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$Extract = Join-Path $TempBase ("mtm-release-acceptance-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Extract | Out-Null

try {
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $Extract

    $Roots = @(Get-ChildItem -LiteralPath $Extract -Directory)
    if ($Roots.Count -ne 1) {
        throw "Release archive must contain exactly one top-level directory; found $($Roots.Count)"
    }
    $PackageRoot = $Roots[0].FullName
    $Backend = Join-Path $PackageRoot "backend"

    $Required = @(
        (Join-Path $PackageRoot "START.bat"),
        (Join-Path $PackageRoot "STOP.bat"),
        (Join-Path $Backend "run_server.py"),
        (Join-Path $Backend "requirements.lock"),
        (Join-Path $Backend ".env.example"),
        (Join-Path $Backend "static\index.html")
    )
    foreach ($Path in $Required) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "Packaged release is missing required file: $Path"
        }
    }

    $Unsafe = @(Get-ChildItem -LiteralPath $PackageRoot -Recurse -Force | Where-Object {
        $_.Name -in @('.env', 'app.db', 'twofa.bin', 'node_modules', '.venv', '.git') -or
        $_.Name -like '*.session' -or
        $_.Name -like '*.session-wal' -or
        $_.Name -like '*.session-shm' -or
        $_.Name -like '*.session-journal' -or
        $_.Name -like '*.rollback-*'
    })
    if ($Unsafe.Count -gt 0) {
        $Names = ($Unsafe | ForEach-Object FullName) -join [Environment]::NewLine
        throw "Unsafe packaged release contents detected:`n$Names"
    }

    try {
        & $PythonPath (Join-Path $Root "backend\tests\runtime_smoke.py") --backend-root $Backend
        if ($LASTEXITCODE -ne 0) {
            throw "Packaged release runtime smoke failed with exit code $LASTEXITCODE"
        }
    } finally {
        $Log = Join-Path $PackageRoot "runtime-smoke-uvicorn.log"
        if (Test-Path -LiteralPath $Log) {
            Write-Host "===== packaged runtime log ====="
            Get-Content -LiteralPath $Log
        }
    }

    Write-Host "Packaged release acceptance passed: $ZipPath"
} finally {
    Remove-Item -LiteralPath $Extract -Recurse -Force -ErrorAction SilentlyContinue
}
