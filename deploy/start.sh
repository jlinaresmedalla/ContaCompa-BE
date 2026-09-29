#!/usr/bin/env bash
# Container command for the hosted service (Render): migrate, then run the worker and the API in
# one container. If either process dies the other is stopped and the script exits non-zero, so
# Render restarts the whole container instead of serving an API with a dead worker.
set -euo pipefail

alembic upgrade head

python -m contacompa.entrypoints.worker &
worker=$!
uvicorn contacompa.entrypoints.api.app:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" &
api=$!

stopping=0
shutdown() {
  stopping=1
  kill -TERM "$worker" "$api" 2>/dev/null || true
}
trap shutdown TERM INT

# Returns when the first child exits, or when a trapped signal arrives (status above 128).
set +e
wait -n
status=$?
set -e

# Stop whichever process is still running and wait for both to finish.
kill -TERM "$worker" "$api" 2>/dev/null || true
wait || true

if [ "$stopping" -eq 1 ]; then
  echo "start.sh: shutting down on signal"
  exit 0
fi
echo "start.sh: a process exited (status $status); exiting so the host restarts the container" >&2
exit $(( status == 0 ? 1 : status ))
