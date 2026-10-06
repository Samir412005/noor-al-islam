#!/usr/bin/env bash
# مشرف بوت الحارس التفاعلي — يعيد تشغيله تلقائياً إن سقط.
#   • عملية واحدة مستقلة عن بوت نور الإسلام (polling، لا ويب هوك).
#   • يعيد المحاولة كل ٣ ثوانٍ عند أي توقّف.
#   • يقليم سجلّه عند ٢ م.ب حتى لا يملأ القرص.
# ملاحظة: يحتاج GUARDIAN_BOT_TOKEN؛ بدونه يطبع رسالة إرشادية ويخرج (كود 2).
set -u

cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

INTERVAL=3
LOG_FILE="logs/guardian-bot.log"
LOG_MAX_BYTES=2000000
KEEP_BYTES=400000

trim_log() {
  if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    tail -c "$KEEP_BYTES" "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE"
  fi
}

echo "[guardian-bot] $(date -Is) — بدء مشرف بوت الحارس (كل ${INTERVAL}ث عند السقوط)" >> "$LOG_FILE"

while true; do
  trim_log
  echo "[guardian-bot] $(date -Is) — تشغيل بوت الحارس التفاعلي" >> "$LOG_FILE"
  python -u -m guardian.guardian_bot >> "$LOG_FILE" 2>&1
  code=$?
  echo "[guardian-bot] $(date -Is) — توقّف (كود $code). إعادة بعد ${INTERVAL}ث" >> "$LOG_FILE"
  sleep "$INTERVAL"
done
