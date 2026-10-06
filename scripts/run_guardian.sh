#!/usr/bin/env bash
# مشرف الحارس (Noor Guardian): يشغّل حلقة المراقبة ويعيد تشغيلها عند أي سقوط.
#   (setsid nohup bash scripts/run_guardian.sh >/dev/null 2>&1 &)
set -u

cd "$(dirname "$0")/.." || exit 1
mkdir -p logs guardian/state

# قفل بالـPID: مشرف حارس واحد فقط (لا flock — يبقى محتجزاً بابنٍ يتيم)
PIDFILE=logs/run_guardian.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "[run_guardian] $(date -Is) — نسخة أخرى تعمل؛ خروج." >> logs/guardian.log
  exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT INT TERM

INTERVAL=3
LOG_MAX_BYTES=2000000

while true; do
  if [ -f logs/guardian.log ] && [ "$(stat -c%s logs/guardian.log 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    tail -c 200000 logs/guardian.log > logs/guardian.log.tmp 2>/dev/null && mv logs/guardian.log.tmp logs/guardian.log
  fi
  echo "[guardian-supervisor] $(date -Is) — تشغيل الحارس…" >> logs/guardian.log
  python -u -m guardian.daemon >> logs/guardian.log 2>&1
  code=$?
  echo "[guardian-supervisor] $(date -Is) — توقّف (كود $code). إعادة بعد ${INTERVAL}ث" >> logs/guardian.log
  sleep "$INTERVAL"
done
