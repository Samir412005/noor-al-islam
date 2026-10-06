"""تفعيل الذكاء الاصطناعي بأمر واحد — يضبط .env ثم يعيد تشغيل البوت ويتحقّق.

أمثلة:
    # OpenRouter (فيه نماذج مجانية)
    python scripts/enable_llm.py --provider openrouter --key sk-or-...

    # Groq
    python scripts/enable_llm.py --provider groq --key gsk_...

    # OpenAI
    python scripts/enable_llm.py --provider openai --key sk-...

    # نموذج محلي (Ollama) — بلا مفتاح
    python scripts/enable_llm.py --provider ollama --model qwen2.5:3b

    python scripts/enable_llm.py --status
    python scripts/enable_llm.py --disable

السكربت لا يطبع المفتاح ولا يرسله إلى أي مكان: يكتبه في .env فقط، ثم يختبر النداء فعلياً.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ENV_FILE = ROOT / ".env"

PROVIDERS: dict[str, dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "label": "OpenRouter (نماذج مجانية متاحة)",
        "env_key": "OPENROUTER_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "label": "Groq (سريع، طبقة مجانية)",
        "env_key": "GROQ_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "label": "OpenAI",
        "env_key": "OPENAI_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "label": "Google Gemini (واجهة متوافقة مع OpenAI)",
        "env_key": "GEMINI_API_KEY",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:3b",
        "label": "نموذج محلي عبر Ollama (بلا مفتاح)",
        "env_key": "",
    },
    "custom": {
        "base_url": "",
        "model": "",
        "label": "عنوان ونموذج مخصّصان",
        "env_key": "LLM_API_KEY",
    },
}

KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


def read_env() -> str:
    return ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""


def set_env_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text)
    if not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def update_env(values: dict[str, str]) -> None:
    text = read_env()
    for key, value in values.items():
        text = set_env_value(text, key, value)
    ENV_FILE.write_text(text, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)


def current() -> dict[str, str]:
    text = read_env()
    out: dict[str, str] = {}
    for key in KEYS:
        match = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
        out[key] = (match.group(1).strip() if match else "")
    return out


def restart_bot() -> None:
    """يعيد تشغيل عملية البوت؛ المشرف سيرفعه بالكود والإعدادات الجديدة."""
    result = subprocess.run(["pgrep", "-f", "app.main"], capture_output=True, text=True)
    for pid in result.stdout.split():
        if pid.isdigit():
            subprocess.run(["kill", pid], capture_output=True)
    print("↻ أُعيد تشغيل البوت ليقرأ الإعداد الجديد")


async def verify(base_url: str, api_key: str, model: str) -> bool:
    from app.llm import LLM, LLMError

    client = LLM(base_url=base_url, api_key=api_key, model=model, max_tokens=64)
    if not client.enabled:
        print("✗ الإعداد غير مكتمل (تحتاج LLM_BASE_URL و LLM_MODEL، ومفتاحاً إلا للنماذج المحلية).")
        return False
    try:
        answer = await client.chat(
            [
                {"role": "system", "content": "أجب بالعربية بكلمة واحدة."},
                {"role": "user", "content": "قل: جاهز"},
            ],
            max_tokens=32,
        )
    except LLMError as exc:
        print(f"✗ فشل النداء: {exc}")
        return False
    finally:
        await client.aclose()
    print(f"✅ النموذج يردّ: {answer.strip()[:60]}")
    return True


def show_status() -> int:
    values = current()
    base, key, model = values["LLM_BASE_URL"], values["LLM_API_KEY"], values["LLM_MODEL"]
    if not base or not model:
        print("الذكاء الاصطناعي: ⛔ غير مفعّل (البوت يعمل ببحث مباشر في المصادر).")
        print("   للتفعيل: python scripts/enable_llm.py --provider groq --key <مفتاحك>")
        return 0
    masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("(بلا مفتاح — نموذج محلي)" if not key else "***")
    print("الذكاء الاصطناعي: ✅ مفعّل")
    print(f"   العنوان: {base}")
    print(f"   النموذج: {model}")
    print(f"   المفتاح: {masked}")
    return 0


async def main_async(args: argparse.Namespace) -> int:
    if args.status:
        return show_status()

    if args.disable:
        update_env({k: "" for k in KEYS})
        restart_bot()
        print("⛔ أُوقف الذكاء الاصطناعي — البوت يعود للبحث المباشر في المصادر.")
        return 0

    if not args.provider:
        print("حدّد مزوّداً: --provider " + " | ".join(PROVIDERS))
        return 2

    preset = PROVIDERS.get(args.provider)
    if preset is None:
        print(f"✗ مزوّد غير معروف: {args.provider}")
        return 2

    base_url = args.base_url or preset["base_url"]
    model = args.model or preset["model"]
    api_key = args.key or os.environ.get(preset["env_key"], "") if preset["env_key"] else (args.key or "")

    if not base_url or not model:
        print("✗ تحتاج --base-url و --model مع المزوّد المخصّص.")
        return 2
    if not api_key and not any(h in base_url for h in ("127.0.0.1", "localhost")):
        print(f"✗ المفتاح مطلوب لـ{preset['label']} (مصدره: {preset['env_key']} أو --key).")
        return 2

    print(f"→ المزوّد: {preset['label']}")
    print(f"→ النموذج: {model}")
    if not await verify(base_url, api_key, model):
        if args.force:
            print("⚠️ أُكمل رغم فشل التحقّق (--force).")
        else:
            print("لم أُغيّر شيئاً. (استعمل --force للتجاوز.)")
            return 1

    update_env({"LLM_BASE_URL": base_url, "LLM_API_KEY": api_key, "LLM_MODEL": model})
    print("✅ حُفظ الإعداد في .env (بصلاحيات 600، والمفتاح لا يُطبع).")
    restart_bot()
    await asyncio.sleep(14)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "watchdog.py")], check=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="تفعيل الذكاء الاصطناعي لبوت نور الإسلام")
    parser.add_argument("--provider", choices=sorted(PROVIDERS))
    parser.add_argument("--key", help="مفتاح الـAPI (لا يُطبع ولا يُرسل لأي مكان غير المزوّد)")
    parser.add_argument("--model", help="اسم النموذج (يتجاوز الافتراضي)")
    parser.add_argument("--base-url", help="عنوان متوافق مع OpenAI")
    parser.add_argument("--status", action="store_true", help="عرض الحالة")
    parser.add_argument("--disable", action="store_true", help="إيقاف الذكاء")
    parser.add_argument("--force", action="store_true", help="تجاوز فشل التحقّق")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
