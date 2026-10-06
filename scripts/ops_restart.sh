#!/usr/bin/env bash
# إعادة تشغيل نظيفة لكل شيء: يقتل عمليات المشروع ثم يُقلع المشرف والحراسة الدقيقة.
#   bash scripts/ops_restart.sh          # إعادة تشغيل كاملة
#   bash scripts/ops_restart.sh --status # عرض الحالة بلا تغيير
#
# ملاحظة تقنية: القتل يتمّ داخل بايثون بقراءة /proc (لا بـpkill -f) لأن نمط
# pkill يطابق سطر الأمر نفسه فيقتل الجلسة المُشغِّلة — خطأ تكرّر في هذا المشروع.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

python3 - "$@" <<'PY'
import os, signal, subprocess, sys, time

MARKERS = ("app.main", "run_supervisor", "keepalive.sh")
STATUS_ONLY = "--status" in sys.argv


def project_pids() -> list[int]:
    me = os.getpid()
    found = []
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


def listeners() -> list[str]:
    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if ":8080" in line]


pids = project_pids()
if STATUS_ONLY:
    print(f"عمليات المشروع: {len(pids)} {pids}")
    print(f"مستمعون على 8080: {len(listeners())}")
    sys.exit(0)

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
PY

setsid nohup bash scripts/run_supervisor.sh >/dev/null 2>&1 &
setsid nohup bash scripts/keepalive.sh >/dev/null 2>&1 &
sleep "${WAIT_SECONDS:-18}"

python3 - <<'PY'
import subprocess
out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
lines = [l for l in out.splitlines() if ":8080" in l]
print(f"✅ عمليات تخدم على 8080: {len(lines)}")
for line in lines:
    pid = line.split("pid=")[1].split(",")[0] if "pid=" in line else "?"
    print(f"   pid={pid}")
PY
echo "افحص الآن: bash scripts/ops_status.sh"
