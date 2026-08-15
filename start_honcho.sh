#!/bin/bash
# HonchoBench IRL stack startup — honcho's own uvicorn + deriver.
# Uses the DEDICATED bench key (.bench_plexus_key), never the Hermes key.
set -euo pipefail
cd /home/hermes/projects/honchobench

export PLEXUS_API_KEY="$(cat .bench_plexus_key)"
export PLEXUS_BASE_URL="https://plexus.nebulosa-bass.ts.net/v1"
cd _vendor/honcho

echo "$(date -Is) starting api on :8000, deriver on queue"
nohup uv run uvicorn src.main:app --host 127.0.0.1 --port 8000 > /tmp/honcho-api.log 2>&1 &
echo "api pid $!"
nohup uv run python -m src.deriver > /tmp/honcho-deriver.log 2>&1 &
echo "deriver pid $!"
sleep 6
curl -s --max-time 10 http://127.0.0.1:8000/health && echo " <- api health OK"
