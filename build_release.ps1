$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VersionFile = Join-Path $Root "backend\app\version.py"
$Version = ([regex]::Match((Get-Content $VersionFile -Raw), 'APP_VERSION\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $Version) { throw "Could not read APP_VERSION" }

$BackendPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $BackendPython)) {
    throw "Backend virtual environment is missing; run START.bat once before building"
}
Push-Location (Join-Path $Root "backend")
try {
    & $BackendPython -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed" }
    & $BackendPython check_requirements.py
    if ($LASTEXITCODE -ne 0) { throw "Python packages do not match requirements.lock" }
    & $BackendPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Python dependency check failed" }
} finally { Pop-Location }

Push-Location (Join-Path $Root "frontend")
try {
    # Clean, reproducible dependency install from the lockfile before building.
    if (Test-Path "node_modules") { Remove-Item -LiteralPath "node_modules" -Recurse -Force }
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    & npm.cmd audit --audit-level=high
    if ($LASTEXITCODE -ne 0) { throw "npm audit found a high/critical vulnerability" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    Remove-Item -LiteralPath "node_modules" -Recurse -Force -ErrorAction SilentlyContinue
} finally { Pop-Location }

$ReleaseRoot = Join-Path $Root "release"
$Target = Join-Path $ReleaseRoot "multi-tg-manager-$Version"
if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
New-Item -ItemType Directory -Path $Target | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Target "backend") | Out-Null

Copy-Item (Join-Path $Root "backend\app") (Join-Path $Target "backend\app") -Recurse
Copy-Item (Join-Path $Root "backend\static") (Join-Path $Target "backend\static") -Recurse
Copy-Item (Join-Path $Root "backend\alembic") (Join-Path $Target "backend\alembic") -Recurse
Copy-Item (Join-Path $Root "backend\alembic.ini") (Join-Path $Target "backend\alembic.ini")
Copy-Item (Join-Path $Root "backend\requirements.txt") (Join-Path $Target "backend\requirements.txt")
Copy-Item (Join-Path $Root "backend\requirements.lock") (Join-Path $Target "backend\requirements.lock")
Copy-Item (Join-Path $Root "backend\.env.example") (Join-Path $Target "backend\.env.example")
Copy-Item (Join-Path $Root "backend\check_env.ps1") (Join-Path $Target "backend\check_env.ps1")
Copy-Item (Join-Path $Root "start.bat") (Join-Path $Target "START.bat")
Copy-Item (Join-Path $Root "stop.bat") (Join-Path $Target "STOP.bat")
$OptionalRootFiles = @("RESTORE_BACKUP.bat", "SOAK_TEST.bat", "soak_test.ps1")
foreach ($Name in $OptionalRootFiles) {
    $Source = Join-Path $Root $Name
    if (Test-Path -LiteralPath $Source) {
        Copy-Item $Source (Join-Path $Target $Name)
    } else {
        Write-Host "Optional release helper not present; skipping: $Name"
    }
}
Copy-Item (Join-Path $Root "backend\restore_backup.py") (Join-Path $Target "backend\restore_backup.py")
Copy-Item (Join-Path $Root "backend\soak_scheduler.py") (Join-Path $Target "backend\soak_scheduler.py")
Copy-Item (Join-Path $Root "README.md") (Join-Path $Target "README.md")
Copy-Item (Join-Path $Root "LICENSE") (Join-Path $Target "LICENSE")
Copy-Item (Join-Path $Root "backend\check_requirements.py") (Join-Path $Target "backend\check_requirements.py")

Get-ChildItem -LiteralPath $Target -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $Target -Recurse -File -Filter "*.pyc" |
    Remove-Item -Force

Get-ChildItem -LiteralPath $Target -Recurse -Force | Where-Object {
    $_.Name -in @('.env', 'app.db', 'twofa.bin') -or
    $_.Name -like '*.session' -or
    $_.Name -in @('node_modules', '.venv', '.git', 'logs', 'backups')
} | ForEach-Object { throw "Unsafe release artifact detected: $($_.FullName)" }

$Zip = "$Target.zip"
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -LiteralPath $Target -DestinationPath $Zip
Write-Host "Release created: $Zip"
