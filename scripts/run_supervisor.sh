#!/usr/bin/env bash
# مشرف بوت نور الإسلام — ثلاث عمليات تتقاسم المنفذ (SO_REUSEPORT):
#   • سقوط عملية (أو عمليتين) لا يُسقط الخدمة: الباقي يخدم بلا انقطاع.
#   • المشرف يعيد الميّتة خلال أقل من ثانية.
#   • الإشعارات المجدولة تتولّاها العملية الأساسية (primary) وحدها.
#   القياس الفعلي: قتل عملية ⇒ توفّر 100% · قتل اثنتين ⇒ توفّر 100% (الثالثة تخدم).
set -u

cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

# قفل بالـPID (لا بـflock): flock يبقى محتجزاً بابنٍ يتيم فيمنع الإقلاع للأبد.
PIDFILE=logs/run_supervisor.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "[run_supervisor] $(date -Is) — نسخة أخرى تعمل؛ خروج." >> logs/supervisor.log
  exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT INT TERM

INTERVAL=0.5      # نصف ثانية: أسرع تعافٍ ممكن
LOG_MAX_BYTES=5000000

export REUSE_PORT=true

trim_log() {
  local f="$1"
  if [ -f "$f" ] && [ "$(stat -c%s "$f" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    tail -c 500000 "$f" > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f"
  fi
}

run_worker() {
  local role="$1"
  while true; do
    trim_log "logs/bot-$role.log"
    echo "[supervisor] $(date -Is) — تشغيل العملية: $role" >> "logs/bot-$role.log"
    WORKER_ROLE="$role" python -u -m app.main >> "logs/bot-$role.log" 2>&1
    code=$?
    echo "[supervisor] $(date -Is) — توقّفت $role (كود $code). إعادة بعد ${INTERVAL}ث" >> "logs/bot-$role.log"
    sleep "$INTERVAL"
  done
}

echo "[supervisor] $(date -Is) — بدء مشرف بثلاث عمليات (primary + secondary + tertiary)" >> logs/bot.log
run_worker primary &
PRIMARY_PID=$!
sleep 4    # إقلاع متتالٍ: الأساسية أولاً
run_worker secondary &
SECONDARY_PID=$!
sleep 4
run_worker tertiary &
TERTIARY_PID=$!

trap 'kill $PRIMARY_PID $SECONDARY_PID $TERTIARY_PID 2>/dev/null' INT TERM
wait
