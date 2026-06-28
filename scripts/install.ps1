param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python 3.12+ is required but was not found on PATH."
    exit 1
}

$PythonPath = (Get-Command python).Source
$PythonInfo = & python --version 2>&1

function Write-Info { param($msg) Write-Host "→ $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Write-Err { param($msg) Write-Host "✗ $msg" -ForegroundColor Red }

Write-Host ""
Write-Host -Object $(
@"
┌─────────────────────────────────────────────────────────┐
│              vtx-coding-agent installer                 │
├─────────────────────────────────────────────────────────┤
│  Minimalist coding agent harness                       │
└─────────────────────────────────────────────────────────┘
"@
) -ForegroundColor Cyan
Write-Host ""

# Check for active virtual environment
$ActiveVenv = $env:VIRTUAL_ENV
$PipPython = "python"
$InstallTarget = ""

if ($ActiveVenv -and (Test-Path "$ActiveVenv\Scripts\python.exe")) {
    Write-Info "Active virtual environment detected: $ActiveVenv"
    $PipPython = "$ActiveVenv\Scripts\python.exe"
    $InstallTarget = "active_venv"
}

if ($InstallTarget -eq "") {
    Write-Host "Choose installation type:" -ForegroundColor Cyan
    Write-Host "  1) Managed venv at `$env:LOCALAPPDATA\vtx\venv (isolated, recommended)"
    Write-Host "  2) Global install (system/user Python)"
    Write-Host ""

    if ($NonInteractive) {
        $TargetChoice = "1"
    } else {
        $TargetChoice = Read-Host "Enter choice [1-2]"
    }

    switch ($TargetChoice) {
        "1" {
            $InstallTarget = "managed"
            $VenvDir = "$env:LOCALAPPDATA\vtx\venv"
        }
        "2" {
            $InstallTarget = "global"
        }
        default {
            Write-Err "Invalid choice. Exiting."
            exit 1
        }
    }
}

if ($InstallTarget -eq "managed") {
    if (Test-Path "$VenvDir\Scripts\python.exe") {
        Write-Info "Using existing managed venv: $VenvDir"
    } else {
        Write-Info "Creating managed virtual environment: $VenvDir"
        python -m venv "$VenvDir"
        Write-Success "Virtual environment created"
    }
    $PipPython = "$VenvDir\Scripts\python.exe"
}

# Upgrade pip
Write-Info "Upgrading pip..."
& $PipPython -m pip install --upgrade pip | Out-Null

# Choose source
Write-Host ""
Write-Host "Choose installation source:" -ForegroundColor Cyan
Write-Host "  1) Stable version from PyPI (recommended)"
Write-Host "  2) Latest from GitHub (main branch)"
Write-Host ""

if ($NonInteractive) {
    $SourceChoice = "1"
} else {
    $SourceChoice = Read-Host "Enter choice [1-2]"
}

switch ($SourceChoice) {
    "1" {
        Write-Info "Installing stable version from PyPI..."
        & $PipPython -m pip install --upgrade vtx-coding-agent
    }
    "2" {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Write-Warn "Git is not installed. GitHub source requires git for 'pip install git+https://...'"
            Write-Info "Install git first, or switch to the PyPI option."
        }
        Write-Info "Installing latest from GitHub..."
        & $PipPython -m pip install --upgrade "git+https://github.com/OEvortex/vtx-coding-agent.git@main"
    }
    default {
        Write-Err "Invalid choice. Exiting."
        exit 1
    }
}

# Link command on PATH for managed installs
if ($InstallTarget -eq "managed") {
    $CommandLinkDir = "$env:LOCALAPPDATA\vtx\bin"
    if (-not (Test-Path $CommandLinkDir)) {
        New-Item -ItemType Directory -Force -Path $CommandLinkDir | Out-Null
    }

    $vtxTarget = Join-Path $VenvDir "Scripts\vtx.exe"
    $vtxLink = Join-Path $CommandLinkDir "vtx.exe"
    if (Test-Path $vtxTarget) {
        if (Test-Path $vtxLink) { Remove-Item $vtxLink -Force }
        New-Item -ItemType SymbolicLink -Path $vtxLink -Target $vtxTarget -ErrorAction SilentlyContinue | Out-Null
    }

    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -notlike "*$CommandLinkDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$CommandLinkDir", "User")
        Write-Warn "$CommandLinkDir added to your user PATH. Restart your terminal to use 'vtx'."
    } else {
        Write-Success "Vtx installed successfully. Run 'vtx' to start."
    }
} else {
    Write-Host ""
    Write-Success "Vtx installed successfully. Run 'vtx' to start."
}
