#!/bin/sh
# Start the recorder and the paper market maker on an always-on machine.
# Safe to rerun: it does nothing if the watchdog is already running, so cron
# can call it every few minutes and after a reboot:
#   @reboot      /path/to/prediction-marks/deploy/start.sh
#   */5 * * * *  /path/to/prediction-marks/deploy/start.sh
# The watchdog then relaunches either loop if it stops.
cd "$(dirname "$0")/.."
mkdir -p data
pgrep -f "^/bin/sh ./watchdog.sh" >/dev/null && exit 0
echo "$(date -u +%FT%TZ) deploy/start.sh: starting watchdog" >> data/watchdog.log
if command -v setsid >/dev/null 2>&1; then
  (nohup setsid ./watchdog.sh >/dev/null 2>&1 &)
else
  (nohup ./watchdog.sh >/dev/null 2>&1 &)
fi
