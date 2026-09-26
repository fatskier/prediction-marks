#!/bin/sh
# Keep the recorder running and on the current proxy. Run from a fresh shell:
#   ./watchdog.sh &
# Every 30 s it relaunches run_tape.sh if nothing is running, and replaces a
# recorder whose HTTPS_PROXY differs from this shell's. A cloud container
# restart moves the proxy port; a recorder that outlives the restart keeps the
# dead port and records nothing while looking healthy.
cd "$(dirname "$0")"
mkdir -p data
while true; do
  pid=$(pgrep -f "^python3 tape.py" | head -1)
  if [ -n "$pid" ] && [ -n "$HTTPS_PROXY" ]; then
    theirs=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | sed -n 's/^HTTPS_PROXY=//p')
    if [ -n "$theirs" ] && [ "$theirs" != "$HTTPS_PROXY" ]; then
      last=$(grep "trades=" data/tape.log | tail -1 | cut -d' ' -f1)
      echo "$(date -u +%FT%TZ) stale proxy $theirs (now $HTTPS_PROXY); last trades line $last UTC; restarting" | tee -a data/watchdog.log >> data/gaps.log
      pkill -f "^/bin/sh ./run_tape.sh"; pkill -f "^python3 tape.py"
      (nohup setsid ./run_tape.sh >/dev/null 2>&1 &)
      sleep 20
      for p in $(pgrep -f "^python3 tape.py"); do
        tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -q "^HTTPS_PROXY=$theirs$" && kill -9 "$p"
      done
    fi
  elif ! pgrep -f "^/bin/sh ./run_tape.sh" >/dev/null; then
    echo "$(date -u +%FT%TZ) recorder loop down, restarting" >> data/watchdog.log
    (nohup setsid ./run_tape.sh >/dev/null 2>&1 &)
  fi
  sleep 30
done
