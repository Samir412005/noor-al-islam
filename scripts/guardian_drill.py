"""تدريب الحارس (Drill): يُحقن أعطالاً حقيقية ويتحقّق أن الحارس يُصلحها فعلاً.

    python scripts/guardian_drill.py            # كل الأعطال
    python scripts/guardian_drill.py --fault webhook

هذا ليس اختباراً نظرياً: يقتل عمليات ويُفرّغ الويب هوك فعلاً، ثم يُشغّل دورة حارس
ويقيس هل عاد كل شيء. النتيجة تُطبع كتقرير نجاح/فشل لكل عطل.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guardian import checks  # noqa: E402
from guardian.config import config  # noqa: E402
from guardian.daemon import run_cycle  # noqa: E402


def _fail(message: str) -> None:
    print(f"   ✗ {message}")


def _ok(message: str) -> None:
    print(f"   ✓ {message}")


def serving() -> int:
    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if f":{config.port}" in line)


def kill_one_worker() -> None:
    pids: list[int] = []
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
        except OSError:
            continue
        if "app.main" in cmd:
            pids.append(int(pid))
    if pids:
        os.kill(pids[0], signal.SIGTERM)


def kill_all_supervisors_and_keepalive() -> None:
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
        except OSError:
            continue
        if "run_supervisor" in cmd or "keepalive.sh" in cmd:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


def clear_webhook() -> None:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{config.bot_token}/deleteWebhook",
        data=json.dumps({"drop_pending_updates": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=25).read()


def rename_data_file() -> Path | None:
    path = config.target_dir / "app" / "data" / "adhkar.json"
    if not path.exists():
        return None
    broken = path.with_suffix(".json.broken")
    path.rename(broken)
    return broken


# ── الأعطال ────────────────────────────────────────────────────────
def fault_worker() -> tuple[str, callable]:
    def inject() -> None:
        print("   ⚡ نحقن: قتل عملية خدمة واحدة")
        kill_one_worker()

    def verify() -> bool:
        return serving() >= 1

    return "قتل عملية خدمة", inject, verify


def fault_processes() -> tuple[str, callable, callable]:
    def inject() -> None:
        print("   ⚡ نحقن: قتل المشرف والحراسة الدقيقة")
        kill_all_supervisors_and_keepalive()

    def verify() -> bool:
        pids = 0
        for pid in (p for p in os.listdir("/proc") if p.isdigit()):
            try:
                cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
            except OSError:
                continue
            if "run_supervisor" in cmd:
                pids += 1
        return pids > 0

    return "قتل المشرف والحراسة", inject, verify


def fault_webhook() -> tuple[str, callable, callable]:
    def inject() -> None:
        print("   ⚡ نحقن: إفراغ الويب هوك (البوت يصير صامتاً — العطل الأصلي)")
        clear_webhook()

    def verify() -> bool:
        status, data = checks._http_json(
            f"https://api.telegram.org/bot{config.bot_token}/getWebhookInfo", timeout=20
        )
        return bool(data.get("ok")) and (data["result"].get("url") or "") == f"{config.base_url}{config.webhook_path}"

    return "إفراغ الويب هوك", inject, verify


def fault_data() -> tuple[str, callable, callable]:
    def inject() -> None:
        print("   ⚡ نحقن: إخفاء ملف بيانات الأذكار")
        rename_data_file()

    def verify() -> bool:
        return (config.target_dir / "app" / "data" / "adhkar.json").exists()

    return "فقدان ملف بيانات", inject, verify


FAULTS = {
    "worker": (fault_worker, 12),
    "processes": (fault_processes, 30),
    "webhook": (fault_webhook, 12),
    "data": (fault_data, 90),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="تدريب الحارس على أعطال حقيقية")
    parser.add_argument("--fault", choices=sorted(FAULTS), help="عطل واحد فقط")
    args = parser.parse_args()

    names = [args.fault] if args.fault else list(FAULTS)
    results: list[tuple[str, bool, str]] = []

    for name in names:
        builder, wait = FAULTS[name]
        label, inject, verify = builder()
        print(f"\n■■ عطل: {label}")
        inject()
        time.sleep(2)

        outcome = run_cycle(config, force_fix=True, verbose=True)
        applied = [item["name"] for item in outcome["applied"]]
        print(f"   إصلاحات نُفّذت: {applied or 'لا شيء'}")
        print(f"   الحالة بعد الإصلاح: {'سليم ✅' if outcome['summary']['healthy'] else 'ما زال: ' + str(outcome['summary']['failed'])}")

        deadline = time.time() + wait
        recovered = False
        while time.time() < deadline:
            if verify():
                recovered = True
                break
            time.sleep(2)
        if recovered:
            _ok("تعافى فعلاً")
        else:
            _fail(f"لم يتعافَ خلال {wait} ثانية")
        results.append((label, recovered, ", ".join(applied) or "—"))

    print("\n" + "═" * 62)
    print("نتيجة التدريب")
    print("═" * 62)
    for label, recovered, actions in results:
        print(f"{'✅' if recovered else '🛑'} {label:28} الإصلاح: {actions}")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} عطل أُصلح تلقائياً")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
