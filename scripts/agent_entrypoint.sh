#!/bin/sh
## Agent container entrypoint.
##
## 1. Wait for IdP /healthz.
## 2. If /app/keys/<agent_id>/private.pem missing → register with IdP via
##    POST /agents/register, save kid + private_pem to disk.
## 3. exec uvicorn with the configured APP_MODULE.

set -eu

: "${AGENT_ID:?AGENT_ID env var required}"
: "${IDP_URL:?IDP_URL env var required}"
: "${APP_MODULE:?APP_MODULE env var required}"
PORT="${PORT:-8000}"
KEY_DIR="/app/keys/${AGENT_ID}"

echo "[entrypoint] waiting for IdP @ ${IDP_URL}/healthz ..."
i=0
until curl -fsS "${IDP_URL}/healthz" > /dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "[entrypoint] timeout waiting for IdP" >&2
        exit 1
    fi
    sleep 1
done
echo "[entrypoint] IdP healthy"

if [ ! -f "${KEY_DIR}/private.pem" ]; then
    echo "[entrypoint] no key yet — registering ${AGENT_ID} with IdP"
    mkdir -p "${KEY_DIR}"
    python -m agents.common.bootstrap_register
    echo "[entrypoint] registration complete"
else
    echo "[entrypoint] reusing existing key at ${KEY_DIR}/private.pem"
fi

echo "[entrypoint] launching ${APP_MODULE} on :${PORT}"
exec uvicorn "${APP_MODULE}" --host 0.0.0.0 --port "${PORT}"
