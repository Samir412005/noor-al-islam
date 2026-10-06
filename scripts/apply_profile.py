"""نشر هويّة البوت على تيليجرام (الوصف + الأوامر + الاسم + الصورة).

لا يُغيّر شيئاً نحو التشغيل (webhook/polling) — لهذا لديك `scripts/setup_webhook.py`.

الاستعمال:
    python scripts/apply_profile.py --info                    # عرض الحالة الحالية
    python scripts/apply_profile.py --apply                   # الوصف + الأوامر
    python scripts/apply_profile.py --photo assets/bot_profile.png
    python scripts/apply_profile.py --name "نور الإسلام"
    python scripts/apply_profile.py --all --photo assets/bot_profile.png

ملاحظات:
- يقرأ BOT_TOKEN من البيئة/‎.env ولا يطبعه أبداً.
- تغيير الاسم ثم إعادته: `--name "الاسم القديم"`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = "https://api.telegram.org/bot{token}/{method}"

DESCRIPTION = (
    "🕌 نور الإسلام — رفيقك اليومي للقرآن والحديث والأذكار ومواقيت الصلاة.\n"
    "ستة وكلاء: القرآن، الحديث، الأذكار، المواقيت، المناسبات، ووكيل العلم.\n"
    "كل آية وحديث بمصدره — ولا فتوى ولا نصّ بلا مرجع."
)
SHORT_DESCRIPTION = "قرآن · حديث · أذكار · مواقيت — بمصادرها 🕌"


def load_env_file() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def token() -> str:
    load_env_file()
    value = (os.environ.get("BOT_TOKEN") or "").strip()
    if not value or ":" not in value:
        sys.exit("❌ BOT_TOKEN غير موجود. ضعه في .env أو البيئة.")
    return value


def call(client: httpx.Client, method: str, **payload):
    response = client.post(API.format(token=token(), method=method), json=payload)
    data = response.json()
    if not data.get("ok"):
        print(f"⚠️ {method}: {data.get('description')}")
    return data


def upload_photo(client: httpx.Client, path: Path) -> None:
    if not path.exists():
        sys.exit(f"❌ ملف الصورة غير موجود: {path}")
    # تيليجرام يتطلّب مُعرّفاً وصفياً (InputProfilePhoto) مع رفع الملف كمرفق مسمّى
    descriptor = json.dumps({"type": "static", "photo": "attach://upload"})
    with path.open("rb") as handle:
        response = client.post(
            API.format(token=token(), method="setMyProfilePhoto"),
            data={"photo": descriptor},
            files={"upload": (path.name, handle, "image/png")},
        )
    try:
        data = response.json()
    except ValueError:  # pragma: no cover
        data = {"ok": False, "description": response.text[:200]}
    if data.get("ok"):
        print(f"✅ تم تحديث صورة البوت ({path.name})")
    else:
        print(f"⚠️ تعذّر تحديث الصورة: {data.get('description')}")
        print("   بديل: افتح @BotFather ← /setuserpic")


def current_commands() -> list[dict[str, str]]:
    try:
        from app.bot import COMMANDS  # مصدر واحد للحقيقة

        return [{"command": name, "description": desc} for name, desc in COMMANDS]
    except Exception:  # pragma: no cover - احتياطي إن لم تتوفّر التبعيات
        return [
            {"command": "start", "description": "رسالة البداية 🕌"},
            {"command": "menu", "description": "القائمة الرئيسية"},
            {"command": "quran", "description": "📖 وكيل القرآن"},
            {"command": "hadith", "description": "📜 وكيل الحديث"},
            {"command": "adhkar", "description": "📿 وكيل الأذكار"},
            {"command": "prayer", "description": "🕌 المواقيت والقبلة"},
            {"command": "occasions", "description": "🌙 المناسبات القادمة"},
            {"command": "ask", "description": "🧠 اسأل وكيل العلم"},
            {"command": "city", "description": "📍 تحديد مدينتك"},
            {"command": "settings", "description": "⚙️ الإعدادات"},
            {"command": "help", "description": "📘 المساعدة"},
            {"command": "about", "description": "ℹ️ عن البوت"},
        ]


def show_info(client: httpx.Client) -> None:
    for method in ("getMe", "getMyDescription", "getMyShortDescription", "getMyCommands", "getWebhookInfo"):
        data = call(client, method)
        result = data.get("result")
        if method == "getWebhookInfo" and isinstance(result, dict):
            result = {k: v for k, v in result.items() if k != "url"}
            result["url_host"] = (data.get("result", {}).get("url") or "")[:60]
        print(f"\n── {method} ──")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])


def main() -> int:
    parser = argparse.ArgumentParser(description="نشر هويّة بوت نور الإسلام")
    parser.add_argument("--info", action="store_true", help="عرض الحالة الحالية")
    parser.add_argument("--apply", action="store_true", help="ضبط الوصف والأوامر")
    parser.add_argument("--photo", type=Path, help="مسار صورة البوت (PNG)")
    parser.add_argument("--name", help="تغيير اسم البوت")
    parser.add_argument("--all", action="store_true", help="كل ما سبق")
    args = parser.parse_args()

    if not any([args.info, args.apply, args.photo, args.name, args.all]):
        parser.print_help()
        return 1

    with httpx.Client(timeout=30.0) as client:
        if args.info or args.all:
            show_info(client)

        if args.apply or args.all:
            call(client, "setMyDescription", description=DESCRIPTION)
            call(client, "setMyShortDescription", short_description=SHORT_DESCRIPTION)
            commands = current_commands()
            # النطاق الافتراضي أيضاً: وإلا بقيت قائمة خدمة سابقة في المجموعات
            call(client, "setMyCommands", commands=commands)
            call(client, "setMyCommands", commands=commands, scope={"type": "all_private_chats"})
            print(f"✅ الوصف + الوصف المختصر + {len(commands)} أمراً (نطاقان)")

        if args.name or args.all:
            if args.name:
                data = call(client, "setMyName", name=args.name)
                if data.get("ok"):
                    print(f"✅ الاسم: {args.name}")

        target = args.photo or (ROOT / "assets" / "bot_profile.png" if args.all else None)
        if target:
            upload_photo(client, Path(target))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
