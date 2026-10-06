#!/usr/bin/env bash
# مشرف تشغيل بوت نور الإسلام: يعيد التشغيل تلقائياً عند أي سقوط.
# الاستعمال:  (setsid nohup bash scripts/run_supervisor.sh >/dev/null 2>&1 &)
set -u

cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

INTERVAL=5
while true; do
  echo "[supervisor] $(date -Is) — تشغيل البوت…" >> logs/bot.log
  python -u -m app.main >> logs/bot.log 2>&1
  code=$?
  echo "[supervisor] $(date -Is) — توقّف (كود $code). إعادة المحاولة بعد ${INTERVAL}ث" >> logs/bot.log
  sleep "$INTERVAL"
done
