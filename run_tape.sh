#!/bin/sh
# Keep tape.py running; restart it if it exits. Stop with: pkill -f run_tape.sh; pkill -f tape.py
cd "$(dirname "$0")"
mkdir -p data
while true; do
  echo "$(date -u +%FT%TZ) start" >> data/tape.log
  python3 tape.py --out data/tape "$@" >> data/tape.log 2>&1
  echo "$(date -u +%FT%TZ) exit $?" >> data/tape.log
  sleep 10
done
