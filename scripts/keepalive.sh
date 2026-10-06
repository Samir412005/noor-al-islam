#!/usr/bin/env bash
# حراسة كل ٦٠ ثانية — لا تحتاج أي جلسة وكيل، تعمل داخل الساندبوكس مباشرة.
# تفحص: المشرف + عملية البوت + نقطة /health العامة + مطابقة الويب هوك، وتُصلح فوراً.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

INTERVAL=60
LOG_MAX_BYTES=2000000

while true; do
  if [ -f logs/keepalive.log ] && [ "$(stat -c%s logs/keepalive.log 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    tail -c 200000 logs/keepalive.log > logs/keepalive.log.tmp && mv logs/keepalive.log.tmp logs/keepalive.log
  fi
  python -u scripts/watchdog.py --quiet >> logs/keepalive.log 2>&1
  sleep "$INTERVAL"
done
