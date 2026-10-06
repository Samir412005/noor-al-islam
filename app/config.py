"""إعدادات التطبيق — تُقرأ من متغيّرات البيئة (أو من ملف .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # اختياري: يسمح بتشغيل المشروع دون تثبيت python-dotenv
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


def _get(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name) or default)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name) or default)
    except ValueError:
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    return _get(name).lower() in {"1", "true", "yes", "on"}


def _get_list(name: str) -> list[int]:
    raw = _get(name).replace(" ", "")
    out: list[int] = []
    for part in raw.split(","):
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


@dataclass(frozen=True)
class Settings:
    # Telegram
    bot_token: str = field(default_factory=lambda: _get("BOT_TOKEN"))
    admin_ids: tuple[int, ...] = field(default_factory=lambda: tuple(_get_list("ADMIN_IDS")))

    # التشغيل
    run_mode: str = field(default_factory=lambda: (_get("RUN_MODE", "polling").lower() or "polling"))
    webhook_base_url: str = field(default_factory=lambda: _get("WEBHOOK_BASE_URL").rstrip("/"))
    webhook_path: str = field(default_factory=lambda: "/" + _get("WEBHOOK_PATH", "/telegram/webhook").strip("/"))
    webhook_secret: str = field(default_factory=lambda: _get("WEBHOOK_SECRET"))
    port: int = field(default_factory=lambda: _get_int("PORT", 8080))

    # قاعدة البيانات
    db_path: str = field(
        default_factory=lambda: _get("DB_PATH", str(BASE_DIR / "data" / "noor.db"))
    )

    # LLM (اختياري)
    llm_base_url: str = field(default_factory=lambda: _get("LLM_BASE_URL").rstrip("/"))
    llm_api_key: str = field(default_factory=lambda: _get("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _get("LLM_MODEL"))
    llm_max_tokens: int = field(default_factory=lambda: _get_int("LLM_MAX_TOKENS", 1200))
    llm_temperature: float = field(default_factory=lambda: _get_float("LLM_TEMPERATURE", 0.2))

    # افتراضيات المستخدم
    default_city: str = field(default_factory=lambda: _get("DEFAULT_CITY", "الجزائر"))
    default_country: str = field(default_factory=lambda: _get("DEFAULT_COUNTRY", "Algeria"))
    default_lat: float = field(default_factory=lambda: _get_float("DEFAULT_LAT", 36.7538))
    default_lon: float = field(default_factory=lambda: _get_float("DEFAULT_LON", 3.0588))
    default_method: int = field(default_factory=lambda: _get_int("DEFAULT_METHOD", 3))
    default_reciter: str = field(default_factory=lambda: _get("DEFAULT_RECITER", "ar.alafasy"))
    default_tz: str = field(default_factory=lambda: _get("DEFAULT_TZ", "Africa/Algiers"))

    # الإشعارات
    daily_push: bool = field(default_factory=lambda: _get_bool("DAILY_PUSH", False))

    @property
    def resolved_llm(self) -> tuple[str, str, str]:
        """(base_url, api_key, model) — مع كشف تلقائي لمفاتيح المزوّدين الشائعة.

        هكذا يكفي أن يوجد OPENROUTER_API_KEY أو GROQ_API_KEY في البيئة ليعمل
        وكيل العلم بلا أي تعديل ملفات.
        """
        if self.llm_base_url and self.llm_model:
            return self.llm_base_url, self.llm_api_key, self.llm_model

        for env_key, base_url, model in (
            ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
            ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
            ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
        ):
            key = _get(env_key)
            if key:
                return base_url, key, model
        return self.llm_base_url, self.llm_api_key, self.llm_model

    @property
    def llm_enabled(self) -> bool:
        """LLM متاح إذا عُرف العنوان والنموذج، و(مفتاح أو عنوان محلي).

        يعتمد على `resolved_llm` حتى يعمل الكشف التلقائي عن مفاتيح المزوّدين.
        """
        base_url, api_key, model = self.resolved_llm
        if not (base_url and model):
            return False
        if api_key:
            return True
        local = ("127.0.0.1", "localhost", "0.0.0.0", "host.docker.internal")
        return any(host in base_url for host in local)

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url}{self.webhook_path}"


settings = Settings()
