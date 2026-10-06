#!/usr/bin/env bash
# المستوى الثاني: من يراقب الحارس؟
#   • إن تجمّد نبض الحارس (heartbeat) أكثر من 3 دقائق ⇒ أعِد تشغيله.
#   • إن لم يكن المشرف يعمل ⇒ شغّله.
#   • يُستدعى من مهمّة مجدولة/كرون (لا يعتمد على أي خدمة أخرى).
set -u

cd "$(dirname "$0")/.." || exit 1
mkdir -p logs guardian/state

STALE_SECONDS=180
NOTE=""

# ── ① نبض الحارس ──────────────────────────────────────────────────
if [ -f guardian/state/heartbeat ]; then
  LAST=$(cat guardian/state/heartbeat 2>/dev/null || echo 0)
  NOW=$(date +%s)
  AGE=$(( NOW - LAST ))
else
  AGE=999999
fi

if [ "$AGE" -gt "$STALE_SECONDS" ]; then
  NOTE="نبض الحارس متأخّر ${AGE}ث ⇒ إعادة تشغيله"
  python3 - <<'PY' >> logs/guardian.log 2>&1
import os, signal
for pid in (p for p in os.listdir("/proc") if p.isdigit()):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
    except OSError:
        continue
    if "guardian.daemon" in cmd or "run_guardian.sh" in cmd:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
PY
  sleep 2
  (setsid nohup bash scripts/run_guardian.sh >/dev/null 2>&1 &)
  sleep 6
fi

# ── ② مشرف الحارس يعمل؟ ──────────────────────────────────────────
if ! pgrep -f "run_guardian\.sh" >/dev/null 2>&1; then
  NOTE="${NOTE:+$NOTE · }مشرف الحارس غير مشغّل ⇒ تشغيله"
  (setsid nohup bash scripts/run_guardian.sh >/dev/null 2>&1 &)
fi

# ── ③ دورة حارس فورية للاطمئنان ─────────────────────────────────
python -u -m guardian.daemon --once >/dev/null 2>&1 || true

if [ -n "$NOTE" ]; then
  echo "[level2] $(date -Is) — $NOTE" >> logs/guardian.log
fi
exit 0
