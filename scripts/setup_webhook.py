#!/usr/bin/env python3
"""إدارة Webhook بوت «نور الإسلام» (NoorIslamBot) عبر Telegram Bot API.

يقرأ الإعدادات من متغيّرات البيئة أو من ملف ``.env`` في جذر المشروع:
``BOT_TOKEN`` و``WEBHOOK_BASE_URL`` و``WEBHOOK_PATH`` و``WEBHOOK_SECRET``.

الاستعمال::

    python scripts/setup_webhook.py             # setWebhook بالعنوان والسرّ
    python scripts/setup_webhook.py --info      # getWebhookInfo (بلا طباعة التوكن)
    python scripts/setup_webhook.py --delete    # deleteWebhook (رجوعاً إلى polling)
    python scripts/setup_webhook.py --drop-pending   # حذف التحديثات المنتظرة مع setWebhook

ملاحظة أمنية: هذا السكربت **لا يطبع التوكن أبداً**، ويُنقّي أي رسالة خطأ قبل
عرضها حتى لا يتسرّب التوكن المضمَّن في مسار عنوان Telegram.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_BASE = "https://api.telegram.org"
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 30.0

try:  # httpx مثبّت في متطلّبات المشروع، لكن نتيح بديلاً قياسياً عند غيابه
    import httpx
except Exception:  # pragma: no cover - بيئات بلا httpx
    httpx = None  # type: ignore[assignment]


# ── تحميل الإعدادات ───────────────────────────────────────────────
def load_env() -> None:
    """يحمّل .env بلا الكتابة فوق متغيّرات البيئة القائمة."""
    env_path = ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except Exception:
        pass

    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def webhook_url(base: str, path: str) -> str:
    """يبني العنوان الكامل بنفس منطق app/config.py."""
    base = (base or "").strip().rstrip("/")
    path = "/" + (path or "/telegram/webhook").strip("/")
    return f"{base}{path}"


def mask(value: str, keep: int = 3) -> str:
    """يُخفي قيمة سرّية مع إبقاء طولها للتحقّق البصري."""
    value = value or ""
    if not value:
        return "(فارغ)"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep)} (الطول: {len(value)})"


def sanitize(text: str, token: str) -> str:
    """يمنع تسرّب التوكن في الرسائل المعروضة."""
    text = str(text or "")
    if token:
        text = text.replace(token, "***")
    return text


# ── نداء Telegram ─────────────────────────────────────────────────
async def call(method: str, payload: dict | None = None, token: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
    """ينفّذ نداءً إلى Telegram Bot API ويعيد JSON، أو يرفع RuntimeError مهذّبة."""
    url = f"{API_BASE}/bot{token}/{method}"
    data = {k: v for k, v in (payload or {}).items() if v is not None}

    if httpx is not None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(url, data=data)
            except httpx.HTTPError as exc:  # نوع الاستثناء بلا عنوان URL غالباً
                raise RuntimeError(
                    f"تعذّر الوصول إلى Telegram ({type(exc).__name__}): {sanitize(exc, token)}"
                ) from exc
            body = response.text
    else:  # بديل قياسي
        def _blocking() -> tuple[int, str]:
            encoded = urllib.parse.urlencode(data).encode("utf-8")
            request = urllib.request.Request(url, data=encoded, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as handle:  # noqa: S310
                    return handle.status, handle.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode("utf-8", "replace")

        status_code, body = await asyncio.to_thread(_blocking)
        if status_code >= 400 and not body:
            raise RuntimeError(f"Telegram أعاد الحالة {status_code}")

    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"استجابة غير صالحة من Telegram: {sanitize(body[:200], token)}") from exc

    if not isinstance(parsed, dict) or not parsed.get("ok"):
        description = sanitize(parsed.get("description") or "سبب غير معروف", token)
        raise RuntimeError(f"Telegram رفض الطلب: {description}")
    return parsed


# ── العمليات ──────────────────────────────────────────────────────
async def do_set(token: str, base: str, path: str, secret: str, drop_pending: bool) -> int:
    if not base:
        print("✗ WEBHOOK_BASE_URL فارغ — اضبطه في .env (مثل https://bot.example.com)")
        return 2

    url = webhook_url(base, path)
    if not url.startswith("https://"):
        print(f"⚠️  تحذير: Telegram يشترط HTTPS للـwebhook، والعنوان الحالي: {url}")
    if not secret:
        print("⚠️  تحذير: WEBHOOK_SECRET فارغ — يُنصح بسرّ طويل عشوائي لتأمين نقطة الاستقبال.")

    payload = {"url": url, "secret_token": secret or None}
    if drop_pending:
        payload["drop_pending_updates"] = "true"

    print(f"→ setWebhook: {url}")
    print(f"  WEBHOOK_SECRET: {mask(secret)}")
    try:
        result = await call("setWebhook", payload, token=token)
    except RuntimeError as exc:
        print(f"✗ فشل setWebhook — {exc}")
        return 1

    print(f"✓ نجح التسجيل: {sanitize(result.get('description') or 'Webhook was set', token)}")
    print("  تحقّق لاحقاً بـ: python scripts/setup_webhook.py --info")
    return 0


async def do_delete(token: str, drop_pending: bool) -> int:
    print("→ deleteWebhook (العودة إلى وضع polling)")
    payload = {"drop_pending_updates": "true"} if drop_pending else None
    try:
        result = await call("deleteWebhook", payload, token=token)
    except RuntimeError as exc:
        print(f"✗ فشل deleteWebhook — {exc}")
        return 1
    print(f"✓ {sanitize(result.get('description') or 'Webhook was deleted', token)}")
    print("  شغّل البوت الآن بـ RUN_MODE=polling.")
    return 0


async def do_info(token: str) -> int:
    print("→ getWebhookInfo")
    try:
        result = await call("getWebhookInfo", None, token=token)
    except RuntimeError as exc:
        print(f"✗ فشل getWebhookInfo — {exc}")
        return 1

    info = result.get("result") or {}
    fields = [
        ("العنوان المسجَّل (url)", info.get("url") or "(لا يوجد — وضع polling)"),
        ("شهادة مخصّصة", info.get("has_custom_certificate")),
        ("تحديثات منتظرة", info.get("pending_update_count")),
        ("عنوان IP", info.get("ip_address") or "—"),
        ("آخر خطأ", info.get("last_error_message") or "لا يوجد"),
        ("توقيت آخر خطأ", info.get("last_error_date") or "—"),
        ("أقصى اتصالات متزامنة", info.get("max_connections")),
    ]
    print("  ── حالة الـWebhook ──")
    for label, value in fields:
        if value is None:
            value = "—"
        print(f"  • {label}: {sanitize(str(value), token)}")

    registered = info.get("url") or ""
    base = env("WEBHOOK_BASE_URL")
    if registered and base:
        expected = webhook_url(base, env("WEBHOOK_PATH"))
        if expected != registered:
            print(f"  ⚠️  العنوان المسجَّل يخالف ما في .env — المتوقَّع: {expected}")
    return 0


# ── نقطة الدخول ───────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="إدارة Webhook بوت نور الإسلام (لا يطبع التوكن أبداً)."
    )
    parser.add_argument("--delete", action="store_true", help="deleteWebhook (رجوعاً إلى polling)")
    parser.add_argument("--info", action="store_true", help="طباعة getWebhookInfo فقط")
    parser.add_argument(
        "--drop-pending",
        action="store_true",
        help="حذف التحديثات المنتظرة عند التسجيل/الحذف (drop_pending_updates=true)",
    )
    parser.add_argument(
        "--restore-prohandiq",
        nargs="?",
        const=str(BASE_DIR / "backups" / "original-state.json"),
        metavar="ملف_الحالة",
        help="إعادة الويب هوك إلى الخدمة الخارجية السابقة (يقرأ الرابط من backups/original-state.json)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="مهلة النداء بالثواني (افتراضاً 30)")
    return parser


async def do_restore(token: str, state_file: Path) -> int:
    """يعيد الويب هوك إلى الخدمة الخارجية التي كانت تدير البوت (from backup)."""
    if not state_file.exists():
        print(f"✗ ملف الحالة غير موجود: {state_file}")
        return 2
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"✗ ملف الحالة تالف: {exc}")
        return 2

    template = (state.get("webhook") or {}).get("url_template") or ""
    if not template:
        print("✗ لا يوجد رابط في ملف الحالة.")
        return 2
    url = template.replace("<BOT_TOKEN>", token)
    ok, message = await api(token, "setWebhook", {"url": url, "drop_pending_updates": True})
    print(("✓ " if ok else "✗ ") + message)
    if ok:
        print("  أُعيد الويب هوك إلى:", (state.get("webhook") or {}).get("host"))
    return 0 if ok else 1


async def run(args: argparse.Namespace) -> int:
    load_env()
    token = env("BOT_TOKEN")
    if not token:
        print("✗ BOT_TOKEN غير مضبوط — ضعه في البيئة أو في ملف .env (ولا تشاركه أبداً).")
        return 2

    if args.restore_prohandiq:
        return await do_restore(token, Path(args.restore_prohandiq))

    if args.info:
        return await do_info(token)
    if args.delete:
        return await do_delete(token, args.drop_pending)

    return await do_set(
        token,
        env("WEBHOOK_BASE_URL"),
        env("WEBHOOK_PATH", "/telegram/webhook"),
        env("WEBHOOK_SECRET"),
        args.drop_pending,
    )


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover
        print("\nمُقاطَع.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
