#!/usr/bin/env bash
# مشرف تشغيل بوت نور الإسلام: يعيد التشغيل تلقائياً عند أي سقوط.
# الاستعمال:  (setsid nohup bash scripts/run_supervisor.sh >/dev/null 2>&1 &)
set -u

cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

INTERVAL=5
LOG_MAX_BYTES=5000000   # 5 م.ب: نُقلّص السجلّ بدل أن يتضخّم بلا حدّ

while true; do
  if [ -f logs/bot.log ] && [ "$(stat -c%s logs/bot.log 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    tail -c 500000 logs/bot.log > logs/bot.log.tmp 2>/dev/null && mv logs/bot.log.tmp logs/bot.log
  fi
  echo "[supervisor] $(date -Is) — تشغيل البوت…" >> logs/bot.log
  python -u -m app.main >> logs/bot.log 2>&1
  code=$?
  echo "[supervisor] $(date -Is) — توقّف (كود $code). إعادة المحاولة بعد ${INTERVAL}ث" >> logs/bot.log
  sleep "$INTERVAL"
done
