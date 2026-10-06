"""قياس التوفّر فعلياً: يرصد نقطة الفحص ثم يقتل عمليات خدمة ويعيد الرصد.

    python scripts/measure_availability.py --kill 0     # خطّ الأساس بلا قتل
    python scripts/measure_availability.py --kill 1     # قتل عملية واحدة
    python scripts/measure_availability.py --kill 2     # أسوأ حالة واقعية

يعيد نسبة التوفّر وأطول انقطاع بالثواني — دليل رقمي لا كلام.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import threading
import time
import urllib.request

PORT = 8080
URL = f"http://127.0.0.1:{PORT}/health"


def serving_pids() -> list[int]:
    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    pids: set[int] = set()
    for line in out.splitlines():
        if f":{PORT}" in line:
            for part in line.split("pid=")[1:]:
                pids.add(int(part.split(",")[0]))
    return sorted(pids)


def main() -> int:
    parser = argparse.ArgumentParser(description="قياس توفّر بوت نور الإسلام")
    parser.add_argument("--kill", type=int, default=1, help="عدد العمليات التي تُقتل (0 = بلا قتل)")
    parser.add_argument("--window", type=float, default=20.0, help="مدّة الرصد بالثواني")
    parser.add_argument("--interval", type=float, default=0.25, help="الفاصل بين النبضات")
    args = parser.parse_args()

    beats = {"ok": 0, "fail": 0, "worst": 0.0}
    stop = {"v": False}

    def poll() -> None:
        last_ok = time.time()
        while not stop["v"]:
            try:
                with urllib.request.urlopen(URL, timeout=1.5) as response:
                    response.read(1)
                beats["ok"] += 1
                last_ok = time.time()
            except Exception:
                beats["fail"] += 1
                beats["worst"] = max(beats["worst"], time.time() - last_ok)
            time.sleep(args.interval)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    time.sleep(2)

    targets = serving_pids()[: args.kill]
    if targets:
        print(f"→ قتل {len(targets)} عملية من {len(serving_pids())}: {targets}")
        for pid in targets:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    time.sleep(args.window)
    stop["v"] = True
    thread.join(timeout=5)

    total = beats["ok"] + beats["fail"]
    availability = 100 * beats["ok"] / total if total else 0.0
    print(f"نبضات: {beats['ok']} ناجحة / {beats['fail']} فاشلة")
    print(f"التوفّر = {availability:.2f}%   (أطول انقطاع {beats['worst']:.2f} ثانية)")
    print(f"عمليات تخدم الآن: {serving_pids()}")
    return 0 if availability >= 99.9 or args.kill == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
