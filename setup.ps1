#Requires -Version 5.1
<#
.SYNOPSIS
  First-time setup for OnCall Agent (Windows PowerShell).

.DESCRIPTION
  One command takes a fresh clone to a running stack: checks prerequisites,
  writes local .env files, starts PostgreSQL via Docker, applies the migration,
  builds and starts the full container stack, and waits for the API to be healthy.
  A local Python venv and UI npm install are only done when -DevSetup is passed
  (a container-only user needs neither).

.PARAMETER SkipDocker
  Skip starting PostgreSQL (use when you manage the database yourself). Note:
  migrations are applied by the Docker runner against the database container, so
  under -SkipDocker you must apply agent-api/migrations/001_schema.sql yourself.

.PARAMETER SkipMigrations
  Skip applying SQL migrations.

.PARAMETER DevSetup
  Additionally set up local development: create the Python virtual environment,
  install backend requirements, and run the UI npm install. Off by default.

.PARAMETER Reset
  Recreate the containers from scratch (down + up --build) WITHOUT touching the
  database. Your data is preserved. Use this to fix a wedged/half-built stack.

.PARAMETER WipeData
  DESTRUCTIVE. Delete the Postgres volume so the database starts empty. This is
  the ONLY option that erases data, and it prompts for typed confirmation.

.PARAMETER Check
  Diagnose the environment (Docker, daemon, Compose version, host ports) and
  exit. Changes nothing on disk. Run this first when setup fails.
#>
param(
    [switch]$SkipDocker,
    [switch]$SkipMigrations,
    [switch]$DevSetup,
    [switch]$Reset,
    [switch]$WipeData,
    [switch]$Check
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$AgentApi = Join-Path $Root "agent-api"
$Ui = Join-Path $Root "ui"
$VenvDir = Join-Path $AgentApi "venv"
$DbContainer = "kyc-agent-db"
$DbVolume = "oncall-agent-postgres-data"
$ReposPath = Split-Path -Parent $Root

# Host ports the stack publishes (uncommon by default; overridable in root .env).
$PortVars = [ordered]@{
    "POSTGRES_HOST_PORT" = 45432
    "API_HOST_PORT"      = 48000
    "UI_HOST_PORT"       = 43000
    "HEADROOM_HOST_PORT" = 48787
}

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

function Invoke-NativeProbe {
    <#
      Run a native command, swallow BOTH streams, and report exit code + output.

      Windows PowerShell 5.1 turns a native command's redirected stderr into a
      NativeCommandError ErrorRecord and PRINTS it, so a bare
      `docker compose version *> $null` dumped a PowerShell stack trace on every
      machine whose docker has no `compose` subcommand - burying the actual
      diagnosis. Silencing ErrorActionPreference for the duration keeps the probe
      quiet, which is the whole point of a probe.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @()
    )

    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) {
        return [pscustomobject]@{ Ok = $false; Code = -1; Output = "$Exe not found" }
    }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $global:LASTEXITCODE = 0
    $out = ""
    try {
        # Merge stderr into stdout inside cmd.exe, BEFORE PowerShell sees either.
        # Doing the redirect in PowerShell instead (`2>&1` or `*> $null`) makes 5.1
        # wrap each stderr line in a NativeCommandError; with the
        # $ErrorActionPreference="Stop" this script sets, that error is TERMINATING
        # and killed the run outright. Silencing the preference alone is not enough:
        # it stops the abort but also drops the stderr text, and that text is the
        # single most useful thing to show someone whose docker lacks the plugin.
        $quoted = ($Arguments | ForEach-Object {
            if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
        }) -join ' '
        $out = (& cmd.exe /c "$Exe $quoted 2>&1" | Out-String)
    }
    catch {
        return [pscustomobject]@{ Ok = $false; Code = -1; Output = $_.Exception.Message }
    }
    finally {
        $ErrorActionPreference = $prevEAP
        $Error.Clear()
    }

    return [pscustomobject]@{
        Ok     = ($LASTEXITCODE -eq 0)
        Code   = $LASTEXITCODE
        Output = $out.Trim()
    }
}

# Resolved once by Require-DockerCompose; every compose call reads this.
$script:ComposeCommand = $null

function Get-DockerComposeCommand {
    if ($script:ComposeCommand) { return $script:ComposeCommand }
    return (Require-DockerCompose)
}

function Require-DockerCompose {
    <#
      Compose v2 ONLY, deliberately: agent-api/Dockerfile is a BuildKit Dockerfile
      (14 `RUN --mount=type=cache` directives). Legacy docker-compose v1 drives
      the classic builder, which cannot parse those, so falling back to v1 does
      not degrade - it fails mid-build with an error that looks nothing like the
      real cause. Say so here instead, BEFORE anything is created on disk.
    #>
    if ($script:ComposeCommand) { return $script:ComposeCommand }

    $v2 = Invoke-NativeProbe -Exe "docker" -Arguments @("compose", "version", "--short")
    if ($v2.Ok) {
        $script:ComposeCommand = @("docker", "compose")
        Write-Ok ("Docker Compose " + $v2.Output + " (docker compose plugin)")
        return $script:ComposeCommand
    }

    $standaloneReport = "not installed"
    if (Test-CommandExists "docker-compose") {
        $standalone = Invoke-NativeProbe -Exe "docker-compose" -Arguments @("version", "--short")
        $standaloneReport = $standalone.Output

        if ($standalone.Ok -and $standalone.Output -match '^v?([2-9]|\d{2,})\.') {
            # Standalone binary that is really v2+, supports BuildKit, so allow it.
            $script:ComposeCommand = @("docker-compose")
            Write-Ok ("Docker Compose " + $standalone.Output + " (standalone)")
            return $script:ComposeCommand
        }

        # Only call it v1 when a 1.x version was actually PARSED. A binary that
        # merely failed to run is not evidence of v1, and mislabelling it sends
        # the reader off to fix the wrong problem.
        if ($standalone.Ok -and $standalone.Output -match '^v?1\.') {
            Write-Fail @"
Docker Compose v1 ($($standalone.Output)) cannot build this project - it needs BuildKit.
    agent-api/Dockerfile uses 'RUN --mount=type=cache', which the v1 builder cannot parse.

    Install Compose v2: https://docs.docker.com/compose/install/
    Then 'docker compose version' should work (a space, not a hyphen).
"@
        }
    }

    # No usable Compose. Show exactly what each form reported - a shim such as
    # Rancher Desktop, Podman or Colima's docker often lacks the compose plugin
    # entirely, and the raw messages are the fastest way to recognise that.
    $dockerVer = Invoke-NativeProbe -Exe "docker" -Arguments @("--version")
    Write-Fail @"
Docker Compose v2 is required and no working Compose was found.

    docker --version   : $($dockerVer.Output)
    'docker compose'   : $($v2.Output)
    'docker-compose'   : $standaloneReport

    This project needs Compose V2 (the 'docker compose' subcommand) because
    agent-api/Dockerfile uses BuildKit cache mounts.
    - Docker Desktop (Windows/Mac): included - update to a recent version.
    - Other runtimes (Rancher, Podman, Colima) or Linux: install the plugin per
      https://docs.docker.com/compose/install/linux/

    Verify with:  docker compose version
"@
}

function Enable-BuildKit {
    <#
      Force BuildKit on for this process, and warn early if it looks unavailable.

      Compose v2 alone is NOT enough. agent-api/Dockerfile uses
      'RUN --mount=type=cache', and if the build is routed through the CLASSIC
      builder it dies with "the --mount option requires BuildKit" - which is what
      happens on a Docker 20.10-era engine where the docker-compose-plugin was
      installed but the docker-buildx-plugin was not. Compose then silently falls
      back to the legacy path.

      Setting these two variables makes the docker CLI use the daemon's built-in
      BuildKit, which every engine since 18.09 ships, so the build works even
      without the buildx plugin present.
    #>
    $env:DOCKER_BUILDKIT = "1"
    $env:COMPOSE_DOCKER_CLI_BUILD = "1"

    $buildx = Invoke-NativeProbe -Exe "docker" -Arguments @("buildx", "version")
    if ($buildx.Ok) {
        Write-Ok ("BuildKit available (" + (($buildx.Output -split '\s+')[1]) + ")")
        return
    }

    # No buildx. DOCKER_BUILDKIT=1 above usually carries the build anyway, so this
    # is a warning rather than a hard stop - but say what to install if it fails.
    Write-Warn "docker buildx not found - forcing DOCKER_BUILDKIT=1 instead."
    Write-Host "    If the build still reports 'the --mount option requires BuildKit', install" -ForegroundColor Yellow
    Write-Host "    the buildx plugin (Linux: 'docker-buildx-plugin'; Windows/Mac: update" -ForegroundColor Yellow
    Write-Host "    Docker Desktop) - see https://docs.docker.com/go/buildkit/" -ForegroundColor Yellow
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
    Write-Host "    Waiting for PostgreSQL to become healthy..."
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        # Probe, not a redirect: `docker inspect ... 2>$null` writes a
        # NativeCommandError under this script's $ErrorActionPreference="Stop",
        # which would abort the whole run from inside a retry loop the moment the
        # container is briefly absent.
        $probe = Invoke-NativeProbe -Exe "docker" -Arguments @(
            "inspect", "--format={{.State.Health.Status}}", $DbContainer
        )
        $status = $probe.Output
        if ($status -eq "healthy") {
            Write-Ok "PostgreSQL is healthy"
            return
        }

        Start-Sleep -Seconds 2
    }

    Write-Fail "PostgreSQL did not become healthy in time. Check: docker logs $DbContainer"
}

function Wait-ApiHealthy {
    param([int]$Port)

    $url = "http://localhost:$Port/api/v1/health"
    Write-Host "    Waiting for the API to become healthy ($url)..."
    for ($attempt = 1; $attempt -le 45; $attempt++) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) {
                Write-Ok "API is healthy"
                return $true
            }
        }
        catch {
            # Not up yet - keep polling.
        }
        Start-Sleep -Seconds 2
    }

    Write-Warn "API did not report healthy in time. Check: docker logs kyc-agent-api"
    return $false
}

function Get-AppliedMigrations {
    # Applied filenames as a string array; empty if the table is absent or empty.
    #
    # The stderr redirect has to stay (this query legitimately fails before the
    # tracking table exists), but under $ErrorActionPreference="Stop" a redirected
    # native stderr becomes a TERMINATING NativeCommandError - so psql printing a
    # single notice here would kill setup instead of returning "nothing applied".
    # Relaxing the preference for the call keeps the $LASTEXITCODE check working.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $out = "SELECT filename FROM schema_migrations;" |
            docker exec -i $DbContainer psql -tA -U kycuser -d kycagent 2>$null
    }
    finally {
        $ErrorActionPreference = $prevEAP
        $Error.Clear()
    }
    if ($LASTEXITCODE -ne 0 -or -not $out) {
        return @()
    }
    return @($out | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() })
}

function Invoke-MigrationsViaDocker {
    $migrationFiles = Get-ChildItem -Path (Join-Path $AgentApi "migrations") -Filter "*.sql" |
        Sort-Object Name

    if ($migrationFiles.Count -eq 0) {
        Write-Fail "No migration files found in agent-api/migrations"
    }

    # Tracking table so re-runs are fast and never silently replay migrations
    # (or hide a failure) - see also the ON_ERROR_STOP below.
    "CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());" |
        docker exec -i $DbContainer psql -v ON_ERROR_STOP=1 -U kycuser -d kycagent | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Could not create the schema_migrations tracking table."
    }

    $applied = Get-AppliedMigrations

    # Squash baseline: the migration history was squashed into 001_schema.sql,
    # which matches every existing install's live schema. If a core table is
    # present but 001_schema.sql is not recorded - whether the install predates
    # tracking entirely, or recorded the old pre-squash migration filenames -
    # stamp 001_schema.sql as applied WITHOUT executing it (the objects already
    # exist). A fresh volume has no core table, so it falls through and runs it.
    if ($applied -notcontains '001_schema.sql') {
        # Same stderr-abort guard as Get-AppliedMigrations above.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $coreExists = "SELECT to_regclass('public.workflows') IS NOT NULL;" |
                docker exec -i $DbContainer psql -tA -U kycuser -d kycagent 2>$null
        }
        finally {
            $ErrorActionPreference = $prevEAP
            $Error.Clear()
        }
        if ($LASTEXITCODE -eq 0 -and (($coreExists -join '').Trim() -eq 't')) {
            Write-Warn "Existing schema found - stamping 001_schema.sql as applied (not replaying it)."
            "INSERT INTO schema_migrations (filename) VALUES ('001_schema.sql') ON CONFLICT DO NOTHING;" |
                docker exec -i $DbContainer psql -v ON_ERROR_STOP=1 -U kycuser -d kycagent | Out-Null
            $applied = Get-AppliedMigrations
        }
    }

    $appliedCount = 0
    $skippedCount = 0
    foreach ($file in $migrationFiles) {
        if ($applied -contains $file.Name) {
            $skippedCount++
            continue
        }
        Write-Host ("    -> " + $file.Name)
        # ON_ERROR_STOP=1 + no Out-Null: a bad migration now fails loudly with the
        # psql error visible, instead of exiting 0 and being swallowed.
        Get-Content -Path $file.FullName -Raw |
            docker exec -i $DbContainer psql -v ON_ERROR_STOP=1 -U kycuser -d kycagent
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Migration failed: $($file.Name) (see psql output above)"
        }
        "INSERT INTO schema_migrations (filename) VALUES ('$($file.Name)') ON CONFLICT DO NOTHING;" |
            docker exec -i $DbContainer psql -v ON_ERROR_STOP=1 -U kycuser -d kycagent | Out-Null
        $appliedCount++
    }

    Write-Ok "Migrations: $appliedCount applied, $skippedCount already up to date"
}

function Get-RootEnvValue {
    # Read a KEY=value from the root .env, if present. Returns $null when absent.
    param([string]$Key)
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) {
        return $null
    }
    foreach ($line in Get-Content -Path $envFile) {
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts[0].Trim() -eq $Key) {
            return $parts[1].Trim()
        }
    }
    return $null
}

function Get-EffectivePort {
    # Root .env override wins, else the default from $PortVars.
    param([string]$VarName)
    $override = Get-RootEnvValue $VarName
    if ($override -and ($override -match '^\d+$')) {
        return [int]$override
    }
    return [int]$PortVars[$VarName]
}

function Test-ExistingInstall {
    # True if a previous install is present (db volume or db container exists).
    # Probes, not redirects - see the note in Invoke-NativeProbe.
    $vols = Invoke-NativeProbe -Exe "docker" -Arguments @("volume", "ls", "--format", "{{.Name}}")
    if ($vols.Ok -and ($vols.Output -split "`r?`n" | Where-Object { $_.Trim() -eq $DbVolume })) {
        return $true
    }
    $ctrs = Invoke-NativeProbe -Exe "docker" -Arguments @("ps", "-a", "--format", "{{.Names}}")
    return [bool]($ctrs.Ok -and ($ctrs.Output -split "`r?`n" | Where-Object { $_.Trim() -eq $DbContainer }))
}

function Test-HostPorts {
    # Warn/stop if a required host port is already taken by something that is not
    # one of our own containers (which is expected on a second run).
    $conflicts = @()

    foreach ($varName in $PortVars.Keys) {
        $port = Get-EffectivePort $varName
        $listeners = @()
        try {
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop)
        }
        catch {
            # No listener on that port (or cmdlet unavailable) - treat as free.
            continue
        }
        if ($listeners.Count -eq 0) {
            continue
        }

        # Is the listener one of our own already-running containers? If so, fine.
        $ownedByUs = $false
        $pids = $listeners | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique
        foreach ($procId in $pids) {
            $procName = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            if ($procName -match 'docker|com\.docker|vpnkit|wslrelay') {
                $ownedByUs = $true
                break
            }
        }
        if ($ownedByUs) {
            continue
        }

        $conflicts += [pscustomobject]@{ Var = $varName; Port = $port }
    }

    if ($conflicts.Count -gt 0) {
        Write-Host ""
        Write-Warn "These host ports are already in use by another program:"
        foreach ($c in $conflicts) {
            $suggested = $c.Port + 1
            Write-Host ("      $($c.Port) - set $($c.Var)=$suggested (or another free port) in the root .env") -ForegroundColor Yellow
        }
        Write-Fail "Free the ports above or override them in .env, then re-run setup.bat"
    }
}

Write-Host ""
Write-Host "OnCall Agent - initial setup" -ForegroundColor White

Write-Step "Checking prerequisites"

# The default path builds and runs everything INSIDE Docker: the UI is compiled
# in ui/Dockerfile and the backend in agent-api/Dockerfile. So Node, npm and a
# local Python are NOT prerequisites for it - they are only needed for
# -DevSetup, which creates a host venv and runs 'npm install' for the Vite dev
# server. Requiring them up front turned "I only have Docker" into a hard
# failure for a toolchain the install never invoked.
$pythonCommand = $null

if ($DevSetup) {
    if (-not (Test-CommandExists "node")) {
        Write-Fail "Node.js is not installed (needed for -DevSetup). Install Node.js 18+ from https://nodejs.org/"
    }
    Write-Ok ("Node.js " + (node --version))

    if (-not (Test-CommandExists "npm")) {
        Write-Fail "npm is not installed (needed for -DevSetup)."
    }
    Write-Ok ("npm " + (npm --version))

    $pythonCommand = Resolve-PythonCommand
    Write-Ok ("Python available via: " + ($pythonCommand -join " "))
}

if (-not $SkipDocker) {
    if (-not (Test-CommandExists "docker")) {
        Write-Fail "Docker is not installed. Install Docker Desktop or rerun with -SkipDocker."
    }
    $dockerVersion = Invoke-NativeProbe -Exe "docker" -Arguments @("--version")
    Write-Ok ($dockerVersion.Output -replace '^Docker version ', 'Docker ' -replace ',.*$', '')

    # A present CLI doesn't mean the daemon is up - check before we depend on it.
    $dockerInfo = Invoke-NativeProbe -Exe "docker" -Arguments @("info", "--format", "{{.ServerVersion}}")
    if (-not $dockerInfo.Ok) {
        Write-Fail @"
Docker is installed but the daemon is not reachable. Start Docker Desktop and re-run setup.bat.

    docker info said: $($dockerInfo.Output)
"@
    }
    Write-Ok "Docker daemon is running"

    # Resolve Compose HERE, during prerequisites - not lazily at first use. This
    # check used to happen inside the "Starting PostgreSQL" step, so a machine
    # without Compose v2 got as far as writing .env files and data directories
    # before failing. Fail before touching the disk.
    Require-DockerCompose | Out-Null
    Enable-BuildKit

    if (Test-ExistingInstall) {
        Write-Ok "Existing installation detected - updating in place (database volume preserved)"
    }
    else {
        Write-Ok "First-time setup"
    }
}

if ($Check) {
    # Diagnose-only: report the ports too, then stop before anything is written.
    if (-not $SkipDocker) {
        Write-Step "Checking host ports"
        Test-HostPorts
        Write-Ok "Host ports available"
    }
    Write-Host ""
    Write-Host "Environment looks good. Run .\setup.bat to install." -ForegroundColor Green
    exit 0
}

if (-not $SkipDocker) {

    $composeFile = Join-Path $Root "docker-compose.yml"

    if ($WipeData) {
        # The ONLY path that deletes data. 'down -v' removes the named volume.
        Write-Step "WipeData requested - this DELETES the database (all data lost)"
        $answer = Read-Host "    Type 'wipe' to confirm erasing the database (anything else cancels)"
        if ($answer -ne "wipe") {
            Write-Fail "Wipe cancelled - nothing was deleted."
        }
        $compose = @(Get-DockerComposeCommand)
        Invoke-External -ExeAndArgs ($compose + @("-f", $composeFile, "down", "-v")) -WorkingDirectory $Root
        Write-Ok "Stack torn down and database volume removed"
    }
    elseif ($Reset) {
        # Recreate containers WITHOUT -v, so the postgres data volume survives.
        Write-Step "Reset requested - recreating containers (database preserved)"
        $compose = @(Get-DockerComposeCommand)
        Invoke-External -ExeAndArgs ($compose + @("-f", $composeFile, "down")) -WorkingDirectory $Root
        Write-Ok "Containers removed; database volume preserved"
    }
}

Write-Step "Creating local configuration"

# Root .env is where port and proxy overrides live; create it if missing so those
# have a home before we bring the stack up.
New-EnvFile `
    -ExamplePath (Join-Path $Root ".env.example") `
    -TargetPath (Join-Path $Root ".env")

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

$RootComposeFile = Join-Path $Root "docker-compose.yml"

if (-not $SkipDocker) {
    Write-Step "Checking host ports are free"
    Test-HostPorts
    Write-Ok "Host ports available"

    Write-Step "Starting PostgreSQL (Docker)"
    $compose = @(Get-DockerComposeCommand)
    Invoke-External -ExeAndArgs ($compose + @("-f", $RootComposeFile, "up", "-d", "postgres")) -WorkingDirectory $Root
    Wait-PostgresHealthy
}

if (-not $SkipMigrations) {
    if ($SkipDocker) {
        # The migration runner applies SQL via the Docker DB container - the single
        # migration mechanism. With -SkipDocker there's no container to target, so
        # the operator applies the squashed baseline against their own database.
        Write-Warn "SkipDocker: apply agent-api/migrations/001_schema.sql to your database manually (the Docker migration runner is skipped)."
    }
    else {
        Write-Step "Applying database migrations (via Docker)"
        Invoke-MigrationsViaDocker
    }
}

if (-not $SkipDocker) {
    Write-Step "Building and starting the full Docker stack (agent-api, headroom, ui)"
    Write-Host "    First run compiles the codegraph engine and the UI - expect several minutes."
    # Re-resolve rather than reuse $compose from the earlier block: the result is
    # cached, so this costs nothing, and it keeps this step working under
    # Set-StrictMode if the block order ever changes.
    $buildCmd = @(Get-DockerComposeCommand) + @("-f", $RootComposeFile, "up", "--build", "-d")
    Write-Host ("    > " + ($buildCmd -join " "))
    Push-Location $Root
    # Tee the output so the diagnosis below can read the ACTUAL failure instead of
    # guessing. EAP is relaxed for the same reason as in Invoke-NativeProbe: the
    # 2>&1 needed to capture the build log would otherwise become a terminating
    # NativeCommandError the moment docker writes a single line to stderr.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $buildLog = @()
    try {
        & $buildCmd[0] @($buildCmd[1..($buildCmd.Length - 1)]) 2>&1 | Tee-Object -Variable buildLog
        $buildExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEAP
        $Error.Clear()
        Pop-Location
    }

    if ($buildExit -ne 0) {
        $buildText = ($buildLog | Out-String)
        Write-Host ""

        if ($buildText -match '(?i)--mount option requires BuildKit|requires BuildKit|buildkit is not') {
            # Do NOT blame the proxy for this one: the cause is stated in the log.
            Write-Warn "The build ran WITHOUT BuildKit, which this project requires."
            Write-Host "    agent-api/Dockerfile uses 'RUN --mount=type=cache'. The classic builder" -ForegroundColor Yellow
            Write-Host "    cannot parse that, so the build stops at the first such line." -ForegroundColor Yellow
            Write-Host "" -ForegroundColor Yellow
            Write-Host "    Setup already exported DOCKER_BUILDKIT=1, so your Docker is overriding it" -ForegroundColor Yellow
            Write-Host "    or is too old to honour it. Fix one of these:" -ForegroundColor Yellow
            Write-Host "      1. Install the buildx plugin:" -ForegroundColor Yellow
            Write-Host "           Linux:  sudo apt-get install docker-buildx-plugin" -ForegroundColor Yellow
            Write-Host "           Win/Mac: update Docker Desktop to a current version" -ForegroundColor Yellow
            Write-Host "      2. Check ~/.docker/config.json and Docker Desktop settings for" -ForegroundColor Yellow
            Write-Host "         'buildkit: false' or a DOCKER_BUILDKIT=0 environment variable." -ForegroundColor Yellow
            Write-Host "      3. Verify with:  docker buildx version" -ForegroundColor Yellow
            Write-Fail "Docker build failed: BuildKit is required but was not used."
        }

        Write-Warn "The Docker build failed. Most often this is the corporate proxy blocking npm/pip."
        Write-Host "    To see the real error (npm/pip message is above the 'exit code: 1' line):" -ForegroundColor Yellow
        Write-Host "       docker compose build ui --progress=plain --no-cache" -ForegroundColor Yellow
        Write-Host "    Then set HTTP_PROXY / HTTPS_PROXY / NO_PROXY (and NPM_REGISTRY if you use an" -ForegroundColor Yellow
        Write-Host "    internal mirror) in the root .env, or configure Docker Desktop's proxy, and re-run." -ForegroundColor Yellow
        Write-Fail "Docker build failed."
    }
    Write-Ok "Full stack is running"

    Write-Step "Waiting for the API to become healthy"
    Wait-ApiHealthy -Port (Get-EffectivePort "API_HOST_PORT") | Out-Null
}

if ($DevSetup) {
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

$uiPort = Get-EffectivePort "UI_HOST_PORT"
$apiPort = Get-EffectivePort "API_HOST_PORT"

if (-not $SkipDocker) {
    Write-Host "Next steps:"
    Write-Host "  The full Docker stack is up and running:"
    Write-Host "       UI:            http://localhost:$uiPort"
    Write-Host "       API health:    http://localhost:$apiPort/api/v1/health"
    Write-Host ""
    Write-Host "  Prefer local dev instead (backend + Vite UI, with hot reload)?"
    Write-Host "  1. Install local dev deps (venv + npm), if you haven't: .\setup.ps1 -DevSetup"
    Write-Host "  2. Stop the containerized agent-api/ui: docker compose stop agent-api ui"
    Write-Host "  3. Edit agent-api/.env if you need AWS profile or provider settings"
    Write-Host "  4. Start the backend:"
    Write-Host "       cd agent-api"
    Write-Host "       .\venv\Scripts\activate"
    Write-Host "       python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000"
    Write-Host "  5. Start the UI (new terminal):"
    Write-Host "       cd ui"
    Write-Host "       npm run dev   # http://localhost:45173"
}
else {
    Write-Host "Next steps (Docker was skipped):"
    Write-Host "  Option A - Full Docker stack (from repo root):"
    Write-Host "       docker compose up --build -d"
    Write-Host "       Open http://localhost:$uiPort"
    Write-Host "  Option B - Local dev (backend + Vite UI):"
    Write-Host "  1. Install local dev deps (venv + npm), if you haven't: .\setup.ps1 -DevSetup"
    Write-Host "  2. Edit agent-api/.env if you need AWS profile or provider settings"
    Write-Host "  3. Start the backend:"
    Write-Host "       cd agent-api"
    Write-Host "       .\venv\Scripts\activate"
    Write-Host "       python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000"
    Write-Host "  4. Start the UI (new terminal):"
    Write-Host "       cd ui"
    Write-Host "       npm run dev   # http://localhost:45173"
    Write-Host ""
    Write-Host "API health check: http://localhost:48000/api/v1/health"
}
Write-Host ""
