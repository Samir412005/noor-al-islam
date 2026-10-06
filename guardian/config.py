"""إعدادات الحارس (Noor Guardian) — خدمة مستقلة تراقب بوت نور الإسلام وتُصلحه."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TARGET = Path("/home/user/.workspace/noor-islam-bot")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def load_target_env(target: Path) -> dict[str, str]:
    """يقرأ .env الخاص بالبوت الهدف (بلا تعديل البيئة)."""
    values: dict[str, str] = {}
    path = target / ".env"
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@dataclass
class GuardianConfig:
    target_dir: Path = field(default_factory=lambda: Path(_env("GUARDIAN_TARGET", str(DEFAULT_TARGET))))
    interval: int = field(default_factory=lambda: _int("GUARDIAN_INTERVAL", 30))
    #: ألّا تتجاوز الإصلاحات هذا العدد في الساعة لكل فحص (منع حلقات الإصلاح)
    fix_budget_per_hour: int = field(default_factory=lambda: _int("GUARDIAN_FIX_BUDGET", 6))
    #: ألّا يُرسل نفس التنبيه قبل انقضاء هذه المدة
    alert_cooldown: int = field(default_factory=lambda: _int("GUARDIAN_ALERT_COOLDOWN", 900))
    #: حدّ الذاكرة لكل عملية (م.ب) — تجاوزه ⇒ إعادة تشغيل نظيفة
    rss_limit_mb: int = field(default_factory=lambda: _int("GUARDIAN_RSS_LIMIT_MB", 450))
    #: أقلّ مساحة حرة على القرص (م.ب)
    disk_min_free_mb: int = field(default_factory=lambda: _int("GUARDIAN_DISK_MIN_MB", 300))
    #: مهلة سقف لعدد التحديثات المنتظرة قبل اعتباره خللاً
    pending_limit: int = field(default_factory=lambda: _int("GUARDIAN_PENDING_LIMIT", 60))
    #: اختبار المسار الكامل (محاكاة تحديث) كل دورة — يمكن تعطيله
    pipeline_probe: bool = field(default_factory=lambda: _bool("GUARDIAN_PIPELINE_PROBE", True))
    #: قنوات التنبيه
    notify_telegram: bool = field(default_factory=lambda: _bool("GUARDIAN_NOTIFY_TELEGRAM", True))
    notify_calendar: bool = field(default_factory=lambda: _bool("GUARDIAN_NOTIFY_CALENDAR", True))
    notify_draft: bool = field(default_factory=lambda: _bool("GUARDIAN_NOTIFY_DRAFT", False))
    calendar_id: str = field(default_factory=lambda: _env("GUARDIAN_CALENDAR_ID", "primary"))
    quiet: bool = field(default_factory=lambda: _bool("GUARDIAN_QUIET", False))
    #: بريد المالك (لحفظ مسودّات التنبيه في بريده)
    owner_email: str = field(
        default_factory=lambda: _env("GUARDIAN_OWNER_EMAIL", "samir412005com@gmail.com")
    )

    #: بوت الحارس نفسه (اختياري: يحتاج توكن من @BotFather)
    guardian_bot_token: str = field(default_factory=lambda: _env("GUARDIAN_BOT_TOKEN"))

    # ── مشتقّات ────────────────────────────────────────────────────
    @property
    def state_dir(self) -> Path:
        return self.target_dir / "guardian" / "state"

    @property
    def status_file(self) -> Path:
        return self.state_dir / "status.json"

    @property
    def heartbeat_file(self) -> Path:
        return self.state_dir / "heartbeat"

    @property
    def incidents_file(self) -> Path:
        return self.state_dir / "incidents.jsonl"

    @property
    def log_file(self) -> Path:
        return self.target_dir / "logs" / "guardian.log"

    @property
    def target_env(self) -> dict[str, str]:
        return load_target_env(self.target_dir)

    @property
    def bot_token(self) -> str:
        return _env("GUARDIAN_BOT_TOKEN_TARGET") or self.target_env.get("BOT_TOKEN", "")

    @property
    def base_url(self) -> str:
        return self.target_env.get("WEBHOOK_BASE_URL", "").rstrip("/")

    @property
    def webhook_path(self) -> str:
        return "/" + self.target_env.get("WEBHOOK_PATH", "/telegram/webhook").strip("/")

    @property
    def webhook_secret(self) -> str:
        return self.target_env.get("WEBHOOK_SECRET", "")

    @property
    def port(self) -> int:
        try:
            return int(self.target_env.get("PORT", "8080"))
        except ValueError:
            return 8080

    @property
    def admin_chat_ids(self) -> list[int]:
        """مستقبلو التنبيه: ADMIN_IDS من البوت + صاحب البوت المسجَّل في قاعدته."""
        ids: list[int] = []
        raw = self.target_env.get("ADMIN_IDS", "")
        for part in raw.replace(" ", "").split(","):
            if part.lstrip("-").isdigit():
                ids.append(int(part))
        for chat_id in self._owner_ids_from_db():
            if chat_id not in ids:
                ids.append(chat_id)
        return ids

    def _owner_ids_from_db(self) -> list[int]:
        """أول مستخدم استعمل البوت فعلاً (صاحبه غالباً). آمنة تماماً عند أي فشل."""
        import sqlite3

        db_path = self.target_dir / self.target_env.get("DB_PATH", "data/noor.db")
        if not db_path.exists():
            return []
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT chat_id FROM users ORDER BY created_at ASC LIMIT 1"
            ).fetchall()
            con.close()
            return [int(r[0]) for r in rows]
        except Exception:
            return []


config = GuardianConfig()
