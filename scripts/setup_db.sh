#!/usr/bin/env bash
# HonchoBench — infra + vendored-honcho setup for the SUPPORTED way (real honcho).
# Stands up pgvector/Postgres + Redis, clones honcho at a pinned commit if missing,
# writes the runtime .env from .env.example, runs migrations, configures embeddings.
#
# Usage:  ./scripts/setup_db.sh
set -euo pipefail

HONCHO_REPO="https://github.com/plastic-labs/honcho"
HONCHO_COMMIT="4448979"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/_vendor/honcho"

echo "== 1/5 vendor honcho @ $HONCHO_COMMIT =="
if [ ! -d "$VENDOR/.git" ]; then
  mkdir -p "$ROOT/_vendor"
  git clone --quiet "$HONCHO_REPO" "$VENDOR"
  git -C "$VENDOR" checkout --quiet "$HONCHO_COMMIT"
else
  echo "already vendored: $VENDOR"
fi

echo "== 2/5 docker infra (pgvector + redis, host network) =="
docker rm -f honcho-bench-db honcho-bench-redis >/dev/null 2>&1 || true
docker run -d --name honcho-bench-db --network host \
  -e POSTGRES_DB=postgres -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_HOST_AUTH_METHOD=trust \
  --shm-size=4g pgvector/pgvector:pg15 -p 5433 >/dev/null
docker run -d --name honcho-bench-redis --network host redis:8.2 >/dev/null
sleep 3
docker exec honcho-bench-db psql -U postgres -h 127.0.0.1 -p 5433 -c "SELECT 1;" >/dev/null \
  && echo "pgvector ready on :5433"

echo "== 3/5 runtime .env (key stays out of git) =="
if [ ! -f "$ROOT/.bench_plexus_key" ]; then
  echo "ERROR: $ROOT/.bench_plexus_key missing — create it with your dedicated Plexus bench key" >&2
  exit 1
fi
if [ ! -f "$VENDOR/.env" ]; then
  cp "$ROOT/.env.example" "$VENDOR/.env"
  echo "wrote $VENDOR/.env from .env.example"
fi

echo "== 4/5 install deps + migrate ==="
cd "$VENDOR"
uv sync --quiet
uv run alembic upgrade head

echo "== 4.5/5 patch judges Plexus-routable (idempotent) =="
python3 "$ROOT/scripts/patch_honcho.py" "$VENDOR"

echo "== 5/5 configure embeddings (nomic 768) =="
uv run python scripts/configure_embeddings.py --yes

echo "== done — next: ./start_honcho.sh (manual) or python -m bench.run (harness) =="
