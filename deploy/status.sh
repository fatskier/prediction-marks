#!/bin/sh
# One-screen health check: processes, last progress lines, recent gaps, disk.
cd "$(dirname "$0")/.."
date -u +"now %FT%TZ"
pgrep -fl "watchdog.sh|run_tape.sh|run_paper.sh|tape.py|paper_mm.py" || echo "nothing running"
echo "tape:  $(grep 'trades=' data/tape.log 2>/dev/null | tail -1)"
echo "paper: $(grep 'markets=' data/paper.log 2>/dev/null | tail -1)"
tail -3 data/gaps.log 2>/dev/null
du -sh data/tape data/paper 2>/dev/null
df -h . | tail -1
