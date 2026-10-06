"""حارس البوت: يتأكّد أن الخدمة حيّة وأن الويب هوك مضبوط، ويُصلح ما انكسر.

يُشغَّل دوريّاً (مهمّة مجدولة أو cron). آمن للتشغيل المتكرّر.

    python scripts/watchdog.py            # فحص + إصلاح
    python scripts/watchdog.py --check    # فحص فقط (رمز خروج 1 إن كان هناك خلل)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / "logs" / "bot.log"
SUPERVISOR = ROOT / "scripts" / "run_supervisor.sh"


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"[watchdog] {stamp} — {message}\n")


def supervisor_alive() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "run_supervisor.sh"], capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def bot_alive() -> bool:
    result = subprocess.run(["pgrep", "-f", "app.main"], capture_output=True, text=True)
    return bool(result.stdout.strip())


def start_supervisor() -> None:
    subprocess.Popen(
        ["setsid", "nohup", "bash", str(SUPERVISOR)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=str(ROOT),
        start_new_session=True,
    )
    log("أُعيد تشغيل المشرف")


def webhook_state(token: str) -> tuple[str, str | None]:
    response = httpx.get(
        f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=20.0
    )
    result = response.json().get("result") or {}
    return result.get("url") or "", result.get("last_error_message")


def assert_webhook(token: str, base: str, path: str, secret: str) -> bool:
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": f"{base.rstrip('/')}/{path.strip('/')}",
            "secret_token": secret or None,
            "drop_pending_updates": False,
            "max_connections": 40,
        },
        timeout=25.0,
    )
    return bool(response.json().get("ok"))


def main() -> int:
    parser = argparse.ArgumentParser(description="حارس تشغيل بوت نور الإسلام")
    parser.add_argument("--check", action="store_true", help="فحص فقط بلا إصلاح")
    args = parser.parse_args()

    load_env()
    token = os.environ.get("BOT_TOKEN", "")
    base = os.environ.get("WEBHOOK_BASE_URL", "")
    path = os.environ.get("WEBHOOK_PATH", "/telegram/webhook")
    secret = os.environ.get("WEBHOOK_SECRET", "")

    problems: list[str] = []

    if not supervisor_alive():
        problems.append("المشرف غير مشغّل")
        if not args.check:
            start_supervisor()
    if not bot_alive():
        problems.append("عملية البوت متوقّفة")

    if token and base:
        try:
            url, error = webhook_state(token)
            expected = f"{base.rstrip('/')}/{path.strip('/')}"
            if url != expected:
                problems.append(f"الويب هوك غير مطابق ({url[:40] or 'فارغ'})")
                if not args.check and assert_webhook(token, base, path, secret):
                    problems.append("→ أُعيد ضبط الويب هوك")
            if error:
                problems.append(f"خطأ تيليجرام: {error[:80]}")
        except httpx.HTTPError as exc:
            problems.append(f"تعذّر فحص الويب هوك: {exc}")

    if problems:
        log(" | ".join(problems))
        print("⚠️ " + " | ".join(problems))
        return 1 if args.check else 0

    print("✅ كل شيء سليم: المشرف والبوت والويب هوك")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
