#!/bin/sh
# Keep paper_mm.py running; restart it if it exits. Exit code 3 means the
# network was unreachable (stale proxy after a container restart): stop and
# let watchdog.sh, started from a fresh shell, relaunch.
cd "$(dirname "$0")"
mkdir -p data
while true; do
  echo "$(date -u +%FT%TZ) start" >> data/paper.log
  python3 paper_mm.py --out data/paper "$@" >> data/paper.log 2>&1
  code=$?
  echo "$(date -u +%FT%TZ) exit $code" >> data/paper.log
  [ "$code" -eq 3 ] && exit 3
  sleep 10
done
