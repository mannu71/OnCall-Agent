#Requires -Version 5.1
<#
.SYNOPSIS
  First-time setup for OnCall Agent (Windows PowerShell).

.DESCRIPTION
  Checks prerequisites, starts PostgreSQL via Docker, applies migrations,
  creates a Python virtual environment, installs dependencies, and writes
  local .env files.

.PARAMETER SkipDocker
  Skip starting PostgreSQL (use when you manage the database yourself).

.PARAMETER SkipMigrations
  Skip applying SQL migrations.

.PARAMETER SkipDeps
  Skip Python and npm dependency installation.
#>
param(
    [switch]$SkipDocker,
    [switch]$SkipMigrations,
    [switch]$SkipDeps
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$AgentApi = Join-Path $Root "agent-api"
$Ui = Join-Path $Root "ui"
$VenvDir = Join-Path $AgentApi "venv"
$DbContainer = "kyc-agent-db"
$ReposPath = Split-Path -Parent $Root

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    OK: $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "    WARN: $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "    ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Get-DockerComposeCommand {
    docker compose version *> $null
    if ($LASTEXITCODE -eq 0) {
        return @("docker", "compose")
    }

    docker-compose version *> $null
    if ($LASTEXITCODE -eq 0) {
        return @("docker-compose")
    }

    Write-Fail "Docker Compose not found. Install Docker Desktop and ensure it is running."
}

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-PythonVersion {
    param([string[]]$PythonCommand)

    $exe = $PythonCommand[0]
    $pythonArgs = @()
    if ($PythonCommand.Length -gt 1) {
        $pythonArgs = $PythonCommand[1..($PythonCommand.Length - 1)]
    }

    $versionText = & $exe @pythonArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    $parts = $versionText.Trim().Split(".")
    if ($parts.Count -lt 2) {
        return $false
    }

    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    return ($major -gt 3) -or ($major -eq 3 -and $minor -ge 12)
}

function Resolve-PythonCommand {
    foreach ($candidate in @("python", "py")) {
        if (-not (Test-CommandExists $candidate)) {
            continue
        }

        if ($candidate -eq "py") {
            if (Test-PythonVersion @("py", "-3.12")) {
                return @("py", "-3.12")
            }
            if (Test-PythonVersion @("py", "-3")) {
                return @("py", "-3")
            }
            continue
        }

        if (Test-PythonVersion @("python")) {
            return @("python")
        }
    }

    Write-Fail "Python 3.12+ is required. Install from https://www.python.org/downloads/"
}

function Invoke-External {
    param(
        [string[]]$ExeAndArgs,
        [string]$WorkingDirectory = $Root
    )

    Write-Host ("    > " + ($ExeAndArgs -join " "))
    Push-Location $WorkingDirectory
    try {
        $exe = $ExeAndArgs[0]
        $cmdArgs = @()
        if ($ExeAndArgs.Length -gt 1) {
            $cmdArgs = $ExeAndArgs[1..($ExeAndArgs.Length - 1)]
        }
        & $exe @cmdArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Command failed: $($ExeAndArgs -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function New-EnvFile {
    param(
        [string]$ExamplePath,
        [string]$TargetPath,
        [hashtable]$Replacements = @{}
    )

    if (Test-Path $TargetPath) {
        Write-Ok "$TargetPath already exists (left unchanged)"
        return
    }

    if (-not (Test-Path $ExamplePath)) {
        Write-Fail "Missing template: $ExamplePath"
    }

    $content = Get-Content -Path $ExamplePath -Raw
    foreach ($key in $Replacements.Keys) {
        $content = $content.Replace($key, [string]$Replacements[$key])
    }

    Set-Content -Path $TargetPath -Value $content -Encoding UTF8
    Write-Ok "Created $TargetPath"
}

function Wait-PostgresHealthy {
    param([string[]]$ComposeCommand)

    Write-Host "    Waiting for PostgreSQL to become healthy..."
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $status = docker inspect --format='{{.State.Health.Status}}' $DbContainer 2>$null
        if ($status -eq "healthy") {
            Write-Ok "PostgreSQL is healthy"
            return
        }

        Start-Sleep -Seconds 2
    }

    Write-Fail "PostgreSQL did not become healthy in time. Check: docker logs $DbContainer"
}

function Invoke-MigrationsViaDocker {
    $migrationFiles = Get-ChildItem -Path (Join-Path $AgentApi "migrations") -Filter "*.sql" |
        Sort-Object Name

    if ($migrationFiles.Count -eq 0) {
        Write-Fail "No migration files found in agent-api/migrations"
    }

    foreach ($file in $migrationFiles) {
        Write-Host ("    -> " + $file.Name)
        Get-Content -Path $file.FullName -Raw | docker exec -i $DbContainer psql -U kycuser -d kycagent | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Migration failed: $($file.Name)"
        }
    }

    Write-Ok "Applied $($migrationFiles.Count) migration(s)"
}

Write-Host ""
Write-Host "OnCall Agent - initial setup" -ForegroundColor White

Write-Step "Checking prerequisites"

if (-not (Test-CommandExists "node")) {
    Write-Fail "Node.js is not installed. Install Node.js 18+ from https://nodejs.org/"
}
Write-Ok ("Node.js " + (node --version))

if (-not (Test-CommandExists "npm")) {
    Write-Fail "npm is not installed."
}
Write-Ok ("npm " + (npm --version))

$pythonCommand = Resolve-PythonCommand
Write-Ok ("Python available via: " + ($pythonCommand -join " "))

if (-not $SkipDocker) {
    if (-not (Test-CommandExists "docker")) {
        Write-Fail "Docker is not installed. Install Docker Desktop or rerun with -SkipDocker."
    }
    Write-Ok "Docker CLI available"
}

Write-Step "Creating local configuration"

$reposPathForEnv = ($ReposPath -replace "\\", "/")
New-EnvFile `
    -ExamplePath (Join-Path $AgentApi ".env.example") `
    -TargetPath (Join-Path $AgentApi ".env") `
    -Replacements @{ "__REPOS_PATH__" = $reposPathForEnv }

New-EnvFile `
    -ExamplePath (Join-Path $Ui ".env.example") `
    -TargetPath (Join-Path $Ui ".env")

$dataDirs = @(
    (Join-Path $AgentApi "data/storage"),
    (Join-Path $AgentApi "data/workflows"),
    (Join-Path $AgentApi "data/logs"),
    (Join-Path $AgentApi "data/config")
)
foreach ($dir in $dataDirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Ensured agent-api/data directories exist"

if (-not $SkipDocker) {
    Write-Step "Starting PostgreSQL (Docker)"
    $compose = Get-DockerComposeCommand
    Invoke-External -ExeAndArgs ($compose + @("-f", (Join-Path $AgentApi "docker-compose.yml"), "up", "-d", "postgres")) -WorkingDirectory $AgentApi
    Wait-PostgresHealthy -ComposeCommand $compose
}

if (-not $SkipMigrations) {
    if ($SkipDocker) {
        Write-Step "Applying database migrations (local psql)"
        Push-Location $AgentApi
        try {
            & cmd /c "run-migration.bat"
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "Migration script failed. Ensure PostgreSQL is running and psql is installed."
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Step "Applying database migrations (via Docker)"
        Invoke-MigrationsViaDocker
    }
}

if (-not $SkipDeps) {
    Write-Step "Installing Python dependencies"
    if (-not (Test-Path $VenvDir)) {
        Invoke-External -ExeAndArgs ($pythonCommand + @("-m", "venv", "venv")) -WorkingDirectory $AgentApi
    }
    else {
        Write-Ok "Python virtual environment already exists"
    }

    $pipPath = Join-Path $VenvDir "Scripts/pip.exe"
    Invoke-External -ExeAndArgs @($pipPath, "install", "-r", "requirements.txt") -WorkingDirectory $AgentApi

    Write-Step "Installing UI dependencies"
    Invoke-External -ExeAndArgs @("npm", "install", "--ignore-scripts") -WorkingDirectory $Ui
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit agent-api/.env if you need AWS profile or provider settings"
Write-Host "  2. Start the backend:"
Write-Host "       cd agent-api"
Write-Host "       .\venv\Scripts\activate"
Write-Host "       python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
Write-Host "  3. Start the UI (new terminal):"
Write-Host "       cd ui"
Write-Host "       npm run dev"
Write-Host ""
Write-Host "API health check: http://localhost:8000/api/v1/health"
Write-Host ""
