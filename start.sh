#!/bin/bash
set -euo pipefail

echo "[start.sh] Starting Agent-Token IdP system..."

# Wait for Redis
echo "[start.sh] Waiting for Redis..."
until redis-cli -u "${REDIS_URL:-redis://localhost:6379}" ping 2>/dev/null; do
    sleep 1
done
echo "[start.sh] Redis ready."

# Wait for OPA
echo "[start.sh] Waiting for OPA..."
until curl -sf "${OPA_URL:-http://localhost:8181}/health" > /dev/null 2>&1; do
    sleep 1
done
echo "[start.sh] OPA ready."

# Start IdP
echo "[start.sh] Starting IdP..."
cd /app
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
