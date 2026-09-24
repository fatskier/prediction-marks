#!/bin/sh
# Keep tape.py running; restart it if it exits. Stop with:
#   pkill -f "^/bin/sh ./run_tape.sh"; pkill -f "^python3 tape.py"
# Exit code 3 means the network was unreachable for ~10 minutes. In a cloud
# session that usually means the proxy moved after a container restart and this
# shell's HTTPS_PROXY is stale, so restarting from here would not help: stop and
# let a supervisor started from a fresh shell relaunch us.
cd "$(dirname "$0")"
mkdir -p data
while true; do
  echo "$(date -u +%FT%TZ) start" >> data/tape.log
  python3 tape.py --out data/tape "$@" >> data/tape.log 2>&1
  code=$?
  echo "$(date -u +%FT%TZ) exit $code" >> data/tape.log
  if [ "$code" -eq 3 ]; then
    echo "$(date -u +%FT%TZ) network unreachable; stopping so a fresh shell can restart" >> data/tape.log
    exit 3
  fi
  sleep 10
done
