#!/bin/sh
# Keep the recorder and the paper market maker running and on the current
# proxy. Run from a fresh shell:
#   ./watchdog.sh &
# Every 30 s, for each process, it relaunches its loop script if nothing is
# running, and replaces a process whose HTTPS_PROXY differs from this shell's.
# A cloud container restart moves the proxy port; a process that outlives the
# restart keeps the dead port and does nothing useful while looking healthy.
cd "$(dirname "$0")"
mkdir -p data

# check <python script> <loop script> <log whose last progress line marks the gap>
check() {
  py=$1; loop=$2; log=$3
  pid=$(pgrep -f "^python3 $py" | head -1)
  if [ -n "$pid" ] && [ -n "$HTTPS_PROXY" ]; then
    theirs=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | sed -n 's/^HTTPS_PROXY=//p')
    if [ -n "$theirs" ] && [ "$theirs" != "$HTTPS_PROXY" ]; then
      last=$(grep -E "trades=|markets=" "$log" 2>/dev/null | tail -1 | cut -d' ' -f1)
      echo "$(date -u +%FT%TZ) $py: stale proxy $theirs (now $HTTPS_PROXY); last progress line $last UTC; restarting" \
        | tee -a data/watchdog.log >> data/gaps.log
      pkill -f "^/bin/sh ./$loop"; pkill -f "^python3 $py"
      (nohup setsid "./$loop" >/dev/null 2>&1 &)
      sleep 20
      for p in $(pgrep -f "^python3 $py"); do
        tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -q "^HTTPS_PROXY=$theirs$" && kill -9 "$p"
      done
    fi
  elif ! pgrep -f "^/bin/sh ./$loop" >/dev/null; then
    echo "$(date -u +%FT%TZ) $loop down, restarting" >> data/watchdog.log
    (nohup setsid "./$loop" >/dev/null 2>&1 &)
  fi
}

while true; do
  check tape.py run_tape.sh data/tape.log
  check paper_mm.py run_paper.sh data/paper.log
  sleep 30
done
