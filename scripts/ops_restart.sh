#!/usr/bin/env bash
# إعادة تشغيل نظيفة لكل شيء: البوت + الحراسة + الحارس + بوت الحارس.
#   bash scripts/ops_restart.sh          # إعادة تشغيل كاملة
#   bash scripts/ops_restart.sh --status # عرض الحالة بلا تغيير
#
# ملاحظة تقنية مهمّة: القتل يتمّ داخل بايثون بقراءة /proc (لا بـpkill -f) لأن نمط
# pkill يطابق سطر الأمر نفسه فيقتل الجلسة المُشغِّلة — خطأ تكرّر في هذا المشروع.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs guardian/state

python3 - "$@" <<'PYEOF'
import os, signal, subprocess, sys, time

MARKERS = (
    "app.main",               # عمليات البوت
    "run_supervisor",         # مشرف البوت
    "keepalive.sh",           # الحراسة الدقيقة
    "guardian.daemon",        # الحارس
    "run_guardian.sh",        # مشرف الحارس
    "guardian.guardian_bot",  # بوت الحارس التفاعلي
    "run_guardian_bot.sh",    # مشرف بوت الحارس
)
STATUS_ONLY = "--status" in sys.argv


def project_pids() -> list[int]:
    me = os.getpid()
    found: list[int] = []
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore").replace("\x00", " ")
        except OSError:
            continue
        if not cmd.strip() or int(pid) == me:
            continue
        if any(marker in cmd for marker in MARKERS):
            found.append(int(pid))
    return sorted(found)


def serving() -> int:
    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if ":8080" in line)


def alive(path: str) -> int | None:
    try:
        pid = int(open(path).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


if STATUS_ONLY:
    print(f"عمليات المشروع: {len(project_pids())}")
    print(f"مستمعون على 8080: {serving()}")
    for label, path in (
        ("مشرف البوت", "logs/run_supervisor.pid"),
        ("الحراسة الدقيقة", "logs/keepalive.pid"),
        ("مشرف الحارس", "logs/run_guardian.pid"),
    ):
        pid = alive(path)
        print(f"{label}: {'يعمل (' + str(pid) + ')' if pid else 'غير مشغّل'}")
    sys.exit(0)

pids = project_pids()
for pid in pids:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
print(f"أُوقفت {len(pids)} عملية (تنظيف كامل)")
time.sleep(2)

for stale in project_pids():
    try:
        os.kill(stale, signal.SIGKILL)
    except OSError:
        pass
PYEOF

setsid nohup bash scripts/run_supervisor.sh >/dev/null 2>&1 &
setsid nohup bash scripts/keepalive.sh >/dev/null 2>&1 &
setsid nohup bash scripts/run_guardian.sh >/dev/null 2>&1 &
setsid nohup bash scripts/run_guardian_bot.sh >/dev/null 2>&1 &
sleep "${WAIT_SECONDS:-22}"

python3 - <<'PYEOF'
import os, subprocess, time

out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
lines = [line for line in out.splitlines() if ":8080" in line]
print(f"✅ عمليات تخدم على 8080: {len(lines)}")


def alive(path: str):
    try:
        pid = int(open(path).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


for label, path in (
    ("مشرف البوت", "logs/run_supervisor.pid"),
    ("الحراسة الدقيقة", "logs/keepalive.pid"),
    ("مشرف الحارس", "logs/run_guardian.pid"),
):
    pid = alive(path)
    print(f"   {label}: {'يعمل (' + str(pid) + ')' if pid else 'غير مشغّل'}")

try:
    heartbeat = int(open("guardian/state/heartbeat").read().strip())
    print(f"   نبض الحارس: قبل {int(time.time()) - heartbeat} ثانية")
except Exception:
    print("   نبض الحارس: غير متوفّر")
PYEOF

echo "افحص الآن: bash scripts/ops_status.sh"
