#!/usr/bin/env bash
# First-time setup for OnCall Agent (Linux / macOS).
#
# One command takes a fresh clone to a running stack: prerequisites, .env files,
# PostgreSQL, the migration, the full container stack, and an API health wait.
# A local Python venv and UI npm install are only done with --dev.
#
# Usage:
#   ./setup.sh
#   ./setup.sh --skip-docker      # you manage the database yourself
#   ./setup.sh --skip-migrations
#   ./setup.sh --dev              # also set up local dev deps (venv + npm)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_API="${ROOT}/agent-api"
UI="${ROOT}/ui"
VENV_DIR="${AGENT_API}/venv"
DB_CONTAINER="kyc-agent-db"
REPOS_PATH="$(dirname "${ROOT}")"

SKIP_DOCKER=0
SKIP_MIGRATIONS=0
DEV_SETUP=0
CHECK_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --skip-docker) SKIP_DOCKER=1 ;;
    --skip-migrations) SKIP_MIGRATIONS=1 ;;
    --dev) DEV_SETUP=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: ./setup.sh [options]

  (no options)        Build and run the whole stack in Docker. Needs ONLY Docker.
  --check             Diagnose the environment and exit. Changes nothing.
  --dev               Also install host dev deps (Python venv + npm) for hot reload.
                      Only this mode needs Node.js and Python 3.12+.
  --skip-docker       You manage PostgreSQL yourself.
  --skip-migrations   Do not apply agent-api/migrations/*.sql.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
      echo "Run './setup.sh --help' for usage."
      exit 1
      ;;
  esac
done

step() {
  echo ""
  echo "==> $1"
}

ok() {
  echo "    OK: $1"
}

warn() {
  echo "    WARN: $1"
}

fail() {
  echo "    ERROR: $1" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "$1 is not installed"
  fi
}

python_version_ok() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

resolve_python() {
  if command -v python3 >/dev/null 2>&1 && python_version_ok python3; then
    echo python3
    return
  fi
  if command -v python >/dev/null 2>&1 && python_version_ok python; then
    echo python
    return
  fi
  fail "Python 3.12+ is required"
}

# Resolved once by require_compose(); every compose call goes through run_compose.
COMPOSE=()

require_compose() {
  # Compose v2 ONLY, deliberately: agent-api/Dockerfile is a BuildKit Dockerfile
  # (14 `RUN --mount=type=cache` directives). Legacy docker-compose v1 drives the
  # classic builder, which cannot parse those, so a v1 "fallback" does not
  # degrade — it fails mid-build with an error that looks nothing like the real
  # cause. Better to say so here than to let someone burn a build on it.
  if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
    ok "Docker Compose $(docker compose version --short 2>/dev/null || echo v2)"
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    local v
    v="$(docker-compose version --short 2>/dev/null || echo unknown)"
    case "$v" in
      2.*|v2.*|[3-9].*|v[3-9].*)
        # Standalone binary that is really v2+ — supports BuildKit, so allow it.
        COMPOSE=(docker-compose)
        ok "Docker Compose ${v} (standalone)"
        return
        ;;
    esac
    fail "Docker Compose v1 (${v}) cannot build this project — it needs BuildKit.
    Install Compose v2, then re-run:  https://docs.docker.com/compose/install/
    On most systems the Docker Desktop / docker-compose-plugin package provides it,
    and the command becomes 'docker compose' (a space, not a hyphen)."
  fi

  fail "Docker Compose not found. Install Compose v2: https://docs.docker.com/compose/install/"
}

enable_buildkit() {
  # Compose v2 alone is NOT enough. agent-api/Dockerfile uses
  # `RUN --mount=type=cache`, and if the build is routed through the CLASSIC
  # builder it dies with "the --mount option requires BuildKit" — which is what
  # happens on a Docker 20.10-era engine that has the docker-compose-plugin but
  # not the docker-buildx-plugin: compose silently falls back to the legacy path.
  #
  # These two exports make the docker CLI use the daemon's built-in BuildKit,
  # present in every engine since 18.09, so the build works without buildx.
  export DOCKER_BUILDKIT=1
  export COMPOSE_DOCKER_CLI_BUILD=1

  if docker buildx version >/dev/null 2>&1; then
    ok "BuildKit available ($(docker buildx version 2>/dev/null | awk '{print $2}'))"
    return
  fi

  warn "docker buildx not found — forcing DOCKER_BUILDKIT=1 instead."
  echo "    If the build still reports 'the --mount option requires BuildKit', install"
  echo "    the buildx plugin (Linux: 'docker-buildx-plugin'; Mac/Windows: update Docker"
  echo "    Desktop) — see https://docs.docker.com/go/buildkit/"
}

run_compose() {
  (cd "$ROOT" && "${COMPOSE[@]}" -f docker-compose.yml "$@")
}

port_prober() {
  for tool in ss lsof netstat; do
    if command -v "$tool" >/dev/null 2>&1; then
      echo "$tool"
      return 0
    fi
  done
  return 1
}

port_in_use() {
  local port="$1"
  case "$2" in
    ss)      ss -ltn 2>/dev/null | grep -qE "[:.]${port}[[:space:]]" ;;
    lsof)    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 ;;
    netstat) netstat -an 2>/dev/null | grep -qE "[:.]${port}[[:space:]]+.*LISTEN" ;;
    *)       return 1 ;;
  esac
}

check_ports() {
  # A busy host port is the most common first-run failure, and compose only
  # reports it AFTER the build. Warn (never fail) — every port is overridable in
  # .env, and ours being "busy" is expected when the stack is already running.
  local prober busy=0 p name
  if ! prober="$(port_prober)"; then
    warn "no ss/lsof/netstat available — skipping the host-port check"
    return 0
  fi
  for entry in "${UI_HOST_PORT:-43000}:UI_HOST_PORT" \
               "${API_HOST_PORT:-48000}:API_HOST_PORT" \
               "${POSTGRES_HOST_PORT:-45432}:POSTGRES_HOST_PORT"; do
    p="${entry%%:*}"; name="${entry##*:}"
    if port_in_use "$p" "$prober"; then
      warn "port ${p} is already in use — set ${name} in .env to something free (or it may be this stack already running)"
      busy=1
    fi
  done
  [[ "$busy" -eq 0 ]] && ok "Host ports ${UI_HOST_PORT:-43000}, ${API_HOST_PORT:-48000}, ${POSTGRES_HOST_PORT:-45432} are free"
  return 0
}

create_env_file() {
  local example_path="$1"
  local target_path="$2"
  local repos_for_env="$3"

  if [[ -f "$target_path" ]]; then
    ok "$target_path already exists (left unchanged)"
    return
  fi

  [[ -f "$example_path" ]] || fail "Missing template: $example_path"
  sed "s|__REPOS_PATH__|${repos_for_env}|g" "$example_path" > "$target_path"
  ok "Created $target_path"
}

wait_for_postgres() {
  echo "    Waiting for PostgreSQL to become healthy..."
  for _ in $(seq 1 30); do
    status="$(docker inspect --format='{{.State.Health.Status}}' "$DB_CONTAINER" 2>/dev/null || true)"
    if [[ "$status" == "healthy" ]]; then
      ok "PostgreSQL is healthy"
      return
    fi
    sleep 2
  done
  fail "PostgreSQL did not become healthy in time. Check: docker logs $DB_CONTAINER"
}

wait_for_api() {
  local port="$1"
  local url="http://localhost:${port}/api/v1/health"
  echo "    Waiting for the API to become healthy (${url})..."
  local getter=""
  if command -v curl >/dev/null 2>&1; then
    getter="curl -fsS -o /dev/null --max-time 3"
  elif command -v wget >/dev/null 2>&1; then
    getter="wget -q -O /dev/null -T 3"
  else
    warn "Neither curl nor wget found; skipping API health wait."
    return 0
  fi
  for _ in $(seq 1 45); do
    if $getter "$url" >/dev/null 2>&1; then
      ok "API is healthy"
      return 0
    fi
    sleep 2
  done
  warn "API did not report healthy in time. Check: docker logs kyc-agent-api"
  return 0
}

psql_exec() {
  # Run SQL (from stdin) in the DB container, stopping on the first error.
  docker exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U kycuser -d kycagent "$@"
}

run_migrations_docker() {
  shopt -s nullglob
  local files=("${AGENT_API}/migrations/"*.sql)
  shopt -u nullglob

  if [[ ${#files[@]} -eq 0 ]]; then
    fail "No migration files found in agent-api/migrations"
  fi

  local sorted_files=()
  mapfile -t sorted_files < <(printf '%s\n' "${files[@]}" | sort)

  # Tracking table so re-runs skip applied files and never silently replay them.
  echo "CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());" \
    | psql_exec >/dev/null || fail "Could not create the schema_migrations tracking table."

  local applied
  applied="$(echo "SELECT filename FROM schema_migrations;" | psql_exec -tA 2>/dev/null || true)"

  # Squash baseline: the migration history was squashed into 001_schema.sql,
  # which matches every existing install's live schema. If a core table is
  # present but 001_schema.sql is not recorded — whether the install predates
  # tracking entirely, or recorded the old pre-squash migration filenames —
  # stamp 001_schema.sql as applied WITHOUT executing it (the objects already
  # exist). A fresh volume has no core table, so it falls through and runs it.
  if ! grep -qxF '001_schema.sql' <<< "$applied"; then
    local core
    core="$(echo "SELECT to_regclass('public.workflows') IS NOT NULL;" | psql_exec -tA 2>/dev/null | tr -d '[:space:]')"
    if [[ "$core" == "t" ]]; then
      warn "Existing schema found — stamping 001_schema.sql as applied (not replaying it)."
      echo "INSERT INTO schema_migrations (filename) VALUES ('001_schema.sql') ON CONFLICT DO NOTHING;" \
        | psql_exec >/dev/null
      applied="$(echo "SELECT filename FROM schema_migrations;" | psql_exec -tA 2>/dev/null || true)"
    fi
  fi

  local applied_count=0 skipped_count=0
  for file in "${sorted_files[@]}"; do
    local name
    name="$(basename "$file")"
    if grep -qxF "$name" <<< "$applied"; then
      skipped_count=$((skipped_count + 1))
      continue
    fi
    echo "    -> $name"
    # ON_ERROR_STOP + no output suppression: a bad migration fails loudly.
    psql_exec < "$file" || fail "Migration failed: $name (see psql output above)"
    echo "INSERT INTO schema_migrations (filename) VALUES ('$name') ON CONFLICT DO NOTHING;" \
      | psql_exec >/dev/null
    applied_count=$((applied_count + 1))
  done

  ok "Migrations: ${applied_count} applied, ${skipped_count} already up to date"
}

echo ""
echo "OnCall Agent - initial setup"

step "Checking prerequisites"

# The default path builds and runs everything INSIDE Docker: the UI is compiled
# in ui/Dockerfile and the backend in agent-api/Dockerfile. So Node, npm and a
# local Python are NOT prerequisites for it — they are only needed for --dev,
# which creates a host venv and runs `npm install` for the Vite dev server.
# Requiring them up front turned "I only have Docker" into a hard failure for a
# toolchain the install never invoked.
PYTHON_BIN=""

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  require_cmd docker
  ok "Docker $(docker --version | sed 's/^Docker version //; s/,.*//')"
  # A present CLI doesn't mean the daemon is up — check before we depend on it.
  if ! docker info >/dev/null 2>&1; then
    fail "Docker is not running. Start Docker Desktop (or 'sudo systemctl start docker') and re-run ./setup.sh."
  fi
  ok "Docker daemon is running"
  require_compose
  enable_buildkit
fi

if [[ "$DEV_SETUP" -eq 1 ]]; then
  require_cmd node
  require_cmd npm
  ok "Node.js $(node --version)"
  ok "npm $(npm --version)"

  PYTHON_BIN="$(resolve_python)"
  ok "Python available via: ${PYTHON_BIN}"
fi

if [[ "$SKIP_DOCKER" -eq 1 && "$DEV_SETUP" -eq 0 ]]; then
  warn "--skip-docker without --dev leaves nothing to install; you probably want --dev too."
fi

# Load .env so the port check honours any overrides already set there.
if [[ -f "${ROOT}/.env" ]]; then
  set -a; . "${ROOT}/.env"; set +a
fi
check_ports

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo ""
  echo "Environment looks good. Run ./setup.sh to install."
  exit 0
fi

step "Creating local configuration"
REPOS_FOR_ENV="${REPOS_PATH//\\//}"
# Root .env holds port/proxy overrides — create it if missing.
create_env_file "${ROOT}/.env.example" "${ROOT}/.env" "$REPOS_FOR_ENV"
create_env_file "${AGENT_API}/.env.example" "${AGENT_API}/.env" "$REPOS_FOR_ENV"
create_env_file "${UI}/.env.example" "${UI}/.env" ""

mkdir -p \
  "${AGENT_API}/data/storage" \
  "${AGENT_API}/data/workflows" \
  "${AGENT_API}/data/logs" \
  "${AGENT_API}/data/config"
ok "Ensured agent-api/data directories exist"

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  step "Starting PostgreSQL (Docker)"
  run_compose up -d postgres
  wait_for_postgres
fi

if [[ "$SKIP_MIGRATIONS" -eq 0 ]]; then
  if [[ "$SKIP_DOCKER" -eq 1 ]]; then
    # The migration runner applies SQL via the Docker DB container - the single
    # migration mechanism. With --skip-docker there's no container to target, so
    # the operator applies the squashed baseline against their own database.
    warn "skip-docker: apply agent-api/migrations/001_schema.sql to your database manually (the Docker migration runner is skipped)."
  else
    step "Applying database migrations (via Docker)"
    run_migrations_docker
  fi
fi

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  step "Building and starting the full Docker stack (agent-api, headroom, ui)"
  echo "    First run compiles the codegraph engine and the UI — expect several minutes."

  # Tee the log so the failure can be DIAGNOSED rather than guessed at. Without
  # this, `set -e` aborted here with no explanation at all.
  BUILD_LOG="$(mktemp)"
  set +e
  run_compose up --build -d 2>&1 | tee "$BUILD_LOG"
  build_rc=${PIPESTATUS[0]}
  set -e

  if [[ "$build_rc" -ne 0 ]]; then
    echo ""
    if grep -qiE 'mount option requires BuildKit|requires BuildKit|buildkit is not' "$BUILD_LOG"; then
      warn "The build ran WITHOUT BuildKit, which this project requires."
      echo "    agent-api/Dockerfile uses 'RUN --mount=type=cache'. The classic builder"
      echo "    cannot parse that, so the build stops at the first such line."
      echo ""
      echo "    Setup already exported DOCKER_BUILDKIT=1, so your Docker is overriding it"
      echo "    or is too old to honour it. Fix one of these:"
      echo "      1. Install the buildx plugin:  sudo apt-get install docker-buildx-plugin"
      echo "         (Mac/Windows: update Docker Desktop to a current version)"
      echo "      2. Check ~/.docker/config.json and the daemon config for 'buildkit: false',"
      echo "         or a DOCKER_BUILDKIT=0 in your environment."
      echo "      3. Verify with:  docker buildx version"
      rm -f "$BUILD_LOG"
      fail "Docker build failed: BuildKit is required but was not used."
    fi
    echo "    The Docker build failed. Most often this is a corporate proxy blocking npm/pip."
    echo "    To see the real error:"
    echo "       docker compose build ui --progress=plain --no-cache"
    echo "    Then set HTTP_PROXY / HTTPS_PROXY / NO_PROXY (and NPM_REGISTRY for an internal"
    echo "    mirror) in the root .env, or configure Docker's proxy, and re-run."
    rm -f "$BUILD_LOG"
    fail "Docker build failed."
  fi
  rm -f "$BUILD_LOG"
  ok "Full stack is running"

  step "Waiting for the API to become healthy"
  wait_for_api "${API_HOST_PORT:-48000}"
fi

if [[ "$DEV_SETUP" -eq 1 ]]; then
  step "Installing Python dependencies"
  if [[ ! -d "$VENV_DIR" ]]; then
    (cd "$AGENT_API" && "$PYTHON_BIN" -m venv venv)
  else
    ok "Python virtual environment already exists"
  fi
  (cd "$AGENT_API" && "${VENV_DIR}/bin/pip" install -r requirements.txt)

  step "Installing UI dependencies"
  (cd "$UI" && npm install --ignore-scripts)
fi

echo ""
echo "Setup complete."
echo ""

UI_PORT="${UI_HOST_PORT:-43000}"
API_PORT="${API_HOST_PORT:-48000}"

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  echo "Next steps:"
  echo "  The full Docker stack is up and running:"
  echo "       UI:            http://localhost:${UI_PORT}"
  echo "       API health:    http://localhost:${API_PORT}/api/v1/health"
  echo ""
  echo "  Prefer local dev instead (backend + Vite UI, with hot reload)?"
  echo "  1. Install local dev deps (venv + npm), if you haven't: ./setup.sh --dev"
  echo "  2. Stop the containerized agent-api/ui: docker compose stop agent-api ui"
  echo "  3. Edit agent-api/.env if you need AWS profile or provider settings"
  echo "  4. Start the backend:"
  echo "       cd agent-api"
  echo "       source venv/bin/activate"
  echo "       python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000"
  echo "  5. Start the UI (new terminal):"
  echo "       cd ui"
  echo "       npm run dev   # http://localhost:45173"
else
  echo "Next steps (Docker was skipped):"
  echo "  Option A — Full Docker stack (from repo root):"
  echo "       docker compose up --build -d"
  echo "       Open http://localhost:${UI_PORT}"
  echo "  Option B — Local dev (backend + Vite UI):"
  echo "  1. Install local dev deps (venv + npm), if you haven't: ./setup.sh --dev"
  echo "  2. Edit agent-api/.env if you need AWS profile or provider settings"
  echo "  3. Start the backend:"
  echo "       cd agent-api"
  echo "       source venv/bin/activate"
  echo "       python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000"
  echo "  4. Start the UI (new terminal):"
  echo "       cd ui"
  echo "       npm run dev   # http://localhost:45173"
  echo ""
  echo "API health check: http://localhost:48000/api/v1/health"
fi
echo ""
