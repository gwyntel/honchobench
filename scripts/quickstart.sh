#!/usr/bin/env bash
# HonchoBench quickstart — one-shot setup.
#
#   1. Creates .env from .env.example if missing
#   2. Builds the container image
#   3. Prints the exact next command to run a bench
#
# Respects DO_NOT_BUILD=1 to skip the (slow) image build (e.g. `make setup` in CI).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env from .env.example"
  echo "    Edit .env to set PLEXUS_API_KEY before running a bench."
else
  echo "==> .env already exists — leaving it untouched."
fi

if [ "${DO_NOT_BUILD:-0}" != "1" ]; then
  echo "==> Building honchobench image..."
  # --network host: some Docker daemons (incl. this box) cannot reach pypi from
  # the default bridge network. Harmless on normal daemons.
  docker compose -f docker-compose.yml build --network host honchobench
fi

cat <<'EOF'

==> HonchoBench is ready. Next, run a bench:

    # default bench (LiquidAI/LFM2_5-2_6B on the Plexus gateway):
    docker compose -f docker-compose.yml run --rm honchobench

    # or the friendlier Makefile wrapper:
    make bench

    # custom model list:
    make bench MODELS=gpt-4o,gpt-4o-mini

Reports (.md + .csv per role) land in ./reports on the host.
EOF
