"""يحاكي تحديث تيليجرام مرسَلاً إلى نقطة الويب هوك — لاختبار النشر بلا حساب ثانٍ.

الاستعمال:
    python scripts/simulate_update.py --text "أذكار الصباح"
    python scripts/simulate_update.py --command start --chat 123456789
    python scripts/simulate_update.py --bad-secret        # يجب أن يُرفض (401/403)

يقرأ العنوان والسرّ من `.env` (WEBHOOK_BASE_URL/WEBHOOK_PATH/WEBHOOK_SECRET).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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


def build_update(chat_id: int, text: str, *, username: str = "simulator") -> dict:
    return {
        "update_id": int(time.time() * 1000) % 2_000_000_000,
        "message": {
            "message_id": 1,
            "from": {"id": chat_id, "is_bot": False, "first_name": "محاكي", "username": username},
            "chat": {"id": chat_id, "type": "private", "first_name": "محاكي"},
            "date": int(time.time()),
            "text": text,
            "entities": (
                [{"offset": 0, "length": len(text.split()[0]), "type": "bot_command"}]
                if text.startswith("/")
                else []
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="محاكاة تحديث تيليجرام إلى الويب هوك")
    parser.add_argument("--text", default="آية اليوم", help="نصّ الرسالة")
    parser.add_argument("--command", help="اختصار: يرسل /<الأمر>")
    parser.add_argument("--chat", type=int, default=999000111, help="معرّف المحادثة")
    parser.add_argument("--bad-secret", action="store_true", help="إرسال سرّ خاطئ (اختبار الأمن)")
    args = parser.parse_args()

    load_env()
    base = (os.environ.get("WEBHOOK_BASE_URL") or "").rstrip("/")
    path = "/" + (os.environ.get("WEBHOOK_PATH") or "/telegram/webhook").strip("/")
    secret = os.environ.get("WEBHOOK_SECRET") or ""
    if not base:
        print("✗ WEBHOOK_BASE_URL غير مضبوط في .env")
        return 2

    url = f"{base}{path}"
    text = f"/{args.command}" if args.command else args.text
    update = build_update(args.chat, text)
    headers = {
        "Content-Type": "application/json",
        "X-Telegram-Bot-Api-Secret-Token": "wrong-secret" if args.bad_secret else secret,
        "X-Request-Id": uuid.uuid4().hex[:8],
    }

    print(f"→ POST {url}")
    print(f"  text={text!r} chat={args.chat} secret={'BAD' if args.bad_secret else 'OK'}")
    try:
        response = httpx.post(url, json=update, headers=headers, timeout=30.0)
    except httpx.HTTPError as exc:
        print(f"✗ تعذّر الوصول: {exc}")
        return 1
    print(f"← HTTP {response.status_code} {response.text[:120]!r}")

    if args.bad_secret:
        ok = response.status_code in (401, 403)
        print("✅ رُفض السرّ الخاطئ (الأمن سليم)" if ok else "⚠️ قُبل السرّ الخاطئ — راجع WEBHOOK_SECRET")
        return 0 if ok else 1

    ok = response.status_code == 200
    print("✅ قُبل التحديث — راجع السجلّ لرؤية ردّ الوكيل" if ok else "✗ لم يُقبل التحديث")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
