param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VersionFile = Join-Path $Root "backend\app\version.py"
$Version = ([regex]::Match((Get-Content $VersionFile -Raw), 'APP_VERSION\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $Version) { throw "Could not read APP_VERSION" }

if ($env:OS -ne "Windows_NT") {
    throw "Windows desktop artifacts must be built on Windows"
}

$Python = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Backend virtual environment is missing"
}

Write-Host "Installing pinned desktop build tools..."
& $Python -m pip install -r (Join-Path $Root "desktop\requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Desktop build dependency install failed" }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Desktop build dependency check failed" }

Write-Host "Building and validating backend/frontend release inputs..."
& (Join-Path $Root "build_release.ps1")
if ($LASTEXITCODE -ne 0) { throw "Source release preparation failed" }

$DistRoot = Join-Path $Root "dist-desktop"
$BuildRoot = Join-Path $Root "build-desktop"
$SpecRoot = Join-Path $Root "spec-desktop"
$ReleaseRoot = Join-Path $Root "release-desktop"
foreach ($Path in @($DistRoot, $BuildRoot, $SpecRoot, $ReleaseRoot)) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path | Out-Null
}

$Launcher = Join-Path $Root "desktop\launcher.py"
$Common = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", "MultiTGManager",
    "--paths", (Join-Path $Root "backend"),
    "--add-data", ((Join-Path $Root "backend\static") + ";backend\static"),
    "--add-data", ((Join-Path $Root "backend\alembic") + ";backend\alembic"),
    "--add-data", ((Join-Path $Root "backend\alembic.ini") + ";backend"),
    "--add-data", ((Join-Path $Root "backend\.env.example") + ";backend"),
    "--collect-all", "webview",
    "--collect-submodules", "app"
)

Write-Host "Building Installer application directory..."
$InstallerDist = Join-Path $DistRoot "installer"
$InstallerWork = Join-Path $BuildRoot "installer"
$InstallerSpec = Join-Path $SpecRoot "installer"
New-Item -ItemType Directory -Path $InstallerDist, $InstallerWork, $InstallerSpec | Out-Null
$InstallerArgs = $Common + @(
    "--onedir",
    "--distpath", $InstallerDist,
    "--workpath", $InstallerWork,
    "--specpath", $InstallerSpec,
    $Launcher
)
& $Python -m PyInstaller @InstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller onedir build failed" }

$InstallerApp = Join-Path $InstallerDist "MultiTGManager"
$InstallerExe = Join-Path $InstallerApp "MultiTGManager.exe"
if (-not (Test-Path -LiteralPath $InstallerExe -PathType Leaf)) {
    throw "Installer application executable was not produced"
}

Write-Host "Building single-file Portable executable..."
$PortableDist = Join-Path $DistRoot "portable"
$PortableWork = Join-Path $BuildRoot "portable"
$PortableSpec = Join-Path $SpecRoot "portable"
New-Item -ItemType Directory -Path $PortableDist, $PortableWork, $PortableSpec | Out-Null
$PortableArgs = $Common + @(
    "--onefile",
    "--distpath", $PortableDist,
    "--workpath", $PortableWork,
    "--specpath", $PortableSpec,
    $Launcher
)
& $Python -m PyInstaller @PortableArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller portable build failed" }

$PortableBuilt = Join-Path $PortableDist "MultiTGManager.exe"
$PortableRelease = Join-Path $ReleaseRoot "MultiTGManager-Portable-$Version-x64.exe"
if (-not (Test-Path -LiteralPath $PortableBuilt -PathType Leaf)) {
    throw "Portable executable was not produced"
}
Move-Item -LiteralPath $PortableBuilt -Destination $PortableRelease -Force

$InstallerRelease = $null
if (-not $SkipInstaller) {
    $ProgramFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
    $ProgramFiles = [Environment]::GetFolderPath("ProgramFiles")
    $IsccCandidates = @(
        (Join-Path $ProgramFilesX86 "Inno Setup 6\ISCC.exe"),
        (Join-Path $ProgramFiles "Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }

    $Iscc = $IsccCandidates | Select-Object -First 1
    if (-not $Iscc) {
        throw "Inno Setup 6 (ISCC.exe) is required to build the Installer"
    }

    Write-Host "Building Windows Installer..."
    $InnoArgs = @(
        "/DMyAppVersion=$Version",
        "/DSourceDir=$InstallerApp",
        "/DOutputDir=$ReleaseRoot",
        (Join-Path $Root "desktop\installer.iss")
    )
    & $Iscc @InnoArgs
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed" }

    $InstallerRelease = Join-Path $ReleaseRoot "MultiTGManager-Setup-$Version-x64.exe"
    if (-not (Test-Path -LiteralPath $InstallerRelease -PathType Leaf)) {
        throw "Installer executable was not produced"
    }
}

Write-Host "Windows desktop artifacts created:"
Write-Host "  Portable: $PortableRelease"
if ($InstallerRelease) {
    Write-Host "  Installer: $InstallerRelease"
}
