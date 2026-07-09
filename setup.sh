#!/usr/bin/env bash
# First-time setup for OnCall Agent (Linux / macOS).
#
# Usage:
#   ./setup.sh
#   ./setup.sh --skip-docker
#   ./setup.sh --skip-migrations
#   ./setup.sh --skip-deps

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_API="${ROOT}/agent-api"
UI="${ROOT}/ui"
VENV_DIR="${AGENT_API}/venv"
DB_CONTAINER="kyc-agent-db"
REPOS_PATH="$(dirname "${ROOT}")"

SKIP_DOCKER=0
SKIP_MIGRATIONS=0
SKIP_DEPS=0

for arg in "$@"; do
  case "$arg" in
    --skip-docker) SKIP_DOCKER=1 ;;
    --skip-migrations) SKIP_MIGRATIONS=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    -h|--help)
      echo "Usage: ./setup.sh [--skip-docker] [--skip-migrations] [--skip-deps]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
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

run_compose() {
  (cd "$ROOT" && docker compose -f docker-compose.yml "$@")
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

  # Pre-existing install predating tracking: record current files as applied
  # (don't replay old migrations against a live schema).
  if [[ -z "$applied" ]]; then
    local core
    core="$(echo "SELECT to_regclass('public.workflows') IS NOT NULL;" | psql_exec -tA 2>/dev/null | tr -d '[:space:]')"
    if [[ "$core" == "t" ]]; then
      warn "Existing schema found without migration tracking — recording current migrations as applied (not replaying them)."
      for file in "${sorted_files[@]}"; do
        echo "INSERT INTO schema_migrations (filename) VALUES ('$(basename "$file")') ON CONFLICT DO NOTHING;" \
          | psql_exec >/dev/null
      done
      ok "Recorded ${#sorted_files[@]} existing migration(s)"
      return
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
require_cmd node
require_cmd npm
ok "Node.js $(node --version)"
ok "npm $(npm --version)"

PYTHON_BIN="$(resolve_python)"
ok "Python available via: ${PYTHON_BIN}"

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  require_cmd docker
  ok "Docker CLI available"
  # A present CLI doesn't mean the daemon is up — check before we depend on it.
  if ! docker info >/dev/null 2>&1; then
    fail "Docker is not running. Start Docker and re-run ./setup.sh."
  fi
  ok "Docker daemon is running"
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
  if docker compose version >/dev/null 2>&1; then
    run_compose up -d postgres
  elif command -v docker-compose >/dev/null 2>&1; then
    (cd "$ROOT" && docker-compose -f docker-compose.yml up -d postgres)
  else
    fail "Docker Compose not found"
  fi
  wait_for_postgres
fi

if [[ "$SKIP_MIGRATIONS" -eq 0 ]]; then
  if [[ "$SKIP_DOCKER" -eq 1 ]]; then
    step "Applying database migrations (local psql)"
    (cd "$AGENT_API" && chmod +x run-migration.sh && ./run-migration.sh)
  else
    step "Applying database migrations (via Docker)"
    run_migrations_docker
  fi
fi

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  step "Building and starting the full Docker stack (agent-api, headroom, ui)"
  if docker compose version >/dev/null 2>&1; then
    run_compose up --build -d
  else
    (cd "$ROOT" && docker-compose -f docker-compose.yml up --build -d)
  fi
  ok "Full stack is running"
fi

if [[ "$SKIP_DEPS" -eq 0 ]]; then
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
  echo "  1. Stop the containerized agent-api/ui: docker compose stop agent-api ui"
  echo "  2. Edit agent-api/.env if you need AWS profile or provider settings"
  echo "  3. Start the backend:"
  echo "       cd agent-api"
  echo "       source venv/bin/activate"
  echo "       python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000"
  echo "  4. Start the UI (new terminal):"
  echo "       cd ui"
  echo "       npm run dev   # http://localhost:45173"
else
  echo "Next steps (Docker was skipped):"
  echo "  Option A — Full Docker stack (from repo root):"
  echo "       docker compose up --build -d"
  echo "       Open http://localhost:${UI_PORT}"
  echo "  Option B — Local dev (backend + Vite UI):"
  echo "  1. Edit agent-api/.env if you need AWS profile or provider settings"
  echo "  2. Start the backend:"
  echo "       cd agent-api"
  echo "       source venv/bin/activate"
  echo "       python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000"
  echo "  3. Start the UI (new terminal):"
  echo "       cd ui"
  echo "       npm run dev   # http://localhost:45173"
  echo ""
  echo "API health check: http://localhost:48000/api/v1/health"
fi
echo ""
