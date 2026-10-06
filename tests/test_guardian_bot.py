"""اختبارات بوت الحارس التفاعلي — بلا شبكة وبلا تيليجرام حقيقي.

نختبر الدوال النقية فقط (تحليل الأوامر، ترجمة أسماء الفحوصات، الكتم، التقطيع،
تحقّق المشرف)، ونحقن `checks`/`fixers` عند الحاجة عبر monkeypatch.
"""
from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

import pytest

from guardian import guardian_bot as gb
from guardian.checks import CheckResult
from guardian.config import GuardianConfig


@pytest.fixture
def cfg(tmp_path):
    """إعداد حارس نظيف بمجلّد مؤقّت — لا يمسّ حالة المشروع الحقيقية."""
    return GuardianConfig(target_dir=tmp_path)


# ── الوحدة قابلة للاستيراد بلا توكن ────────────────────────────────
def test_module_imports_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """الاستيراد لا يفرض توكن: إعادة التحميل بلا توكن لا ترفع استثناءً."""
    monkeypatch.delenv("GUARDIAN_BOT_TOKEN", raising=False)
    module = importlib.reload(gb)
    assert module.config is not None
    assert callable(module.main)
    # التحقّق من التوكن مؤجّل إلى main() ولا يحدث عند الاستيراد
    assert "BotFather" in module.MISSING_TOKEN_MESSAGE


def test_main_without_token_returns_2(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    """لا توكن ⇒ رسالة عربية واضحة ورمز خروج 2، بلا traceback."""
    monkeypatch.setattr(gb.config, "guardian_bot_token", "")
    code = gb.main()
    output = capsys.readouterr().out
    assert code == 2
    assert "@BotFather" in output and "GUARDIAN_BOT_TOKEN" in output
    assert "run_guardian_bot.sh" in output


# ── تحليل الأمر ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/fix supervisor", ("fix", "supervisor")),
        ("/fix@noor_guardian_bot المشرف", ("fix", "المشرف")),
        ("  /status  ", ("status", "")),
        ("/logs 30", ("logs", "30")),
        ("غير أمر", ("", "")),
        ("", ("", "")),
        ("/", ("", "")),
    ],
)
def test_parse_command(text: str, expected: tuple[str, str]) -> None:
    assert gb.parse_command(text) == expected


# ── جدول ترجمة أسماء الفحوصات (عربي/إنجليزي) ───────────────────────
@pytest.mark.parametrize("name", list(gb.CHECK_LABELS_AR))
def test_english_names_resolve_to_themselves(name: str) -> None:
    assert gb.resolve_check_name(name) == name


@pytest.mark.parametrize(("name", "label"), list(gb.CHECK_LABELS_AR.items()))
def test_all_arabic_labels_resolve(name: str, label: str) -> None:
    assert gb.resolve_check_name(label) == name


def test_arabic_aliases_and_diacritics_resolve() -> None:
    assert gb.resolve_check_name("الذاكرة") == "memory"
    assert gb.resolve_check_name("  قاعدة البيانات ") == "database"
    assert gb.resolve_check_name("المشرف") == "supervisor"
    assert gb.resolve_check_name("/disk") == "disk"


def test_unknown_check_resolves_to_none() -> None:
    assert gb.resolve_check_name("لا-يوجد") is None
    assert gb.resolve_check_name("") is None


def test_every_check_maps_to_a_known_fixer() -> None:
    """كل فحص له مُصلح معروف: إمّا في FIXERS أو من الإصلاحات اليدوية."""
    for name in gb.CHECK_LABELS_AR:
        fixer = gb.fixer_for_check(name)
        assert fixer, f"لا مُصلح للفحص {name}"
        assert fixer in gb.fixers.FIXERS or fixer in gb.fixers.MANUAL_ONLY


# ── منطق الكتم ─────────────────────────────────────────────────────
def test_mute_roundtrip(cfg) -> None:
    assert gb.is_muted(cfg) is False
    until = gb.set_mute(cfg, 30)
    assert until > time.time()
    assert gb.mute_until(cfg) == until
    assert gb.is_muted(cfg) is True
    gb.clear_mute(cfg)
    assert gb.mute_until(cfg) == 0
    assert gb.is_muted(cfg) is False


def test_mute_expiry_is_respected(cfg) -> None:
    gb.mute_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    gb.mute_file(cfg).write_text(str(int(time.time()) - 5), encoding="utf-8")
    assert gb.is_muted(cfg) is False  # انتهى الكتم ⇒ لا يُحترم ككتم


def test_mute_clamps_invalid_values(cfg) -> None:
    until = gb.set_mute(cfg, 0)
    assert until > time.time()  # لا كتم أبدي بمدّة صفرية


def test_mute_file_is_simple_timestamp(cfg) -> None:
    gb.set_mute(cfg, 5)
    assert gb.mute_file(cfg).read_text(encoding="utf-8").strip().isdigit()


# ── تحقّق المشرف ───────────────────────────────────────────────────
def test_is_admin_membership() -> None:
    admins = [111, 222, -1003]
    assert gb.is_admin(111, admins) is True
    assert gb.is_admin(222, admins) is True
    assert gb.is_admin(-1003, admins) is True
    assert gb.is_admin(999, admins) is False
    assert gb.is_admin(None, admins) is False


def test_is_admin_uses_config_when_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gb, "config", SimpleNamespace(admin_chat_ids=[7]))
    assert gb.is_admin(7) is True
    assert gb.is_admin(8) is False


# ── تقطيع الرسائل الطويلة ──────────────────────────────────────────
def test_paginate_splits_long_text() -> None:
    text = "\n".join(f"سطر رقم {i}" for i in range(3000))
    parts = gb.paginate(text, limit=1000)
    assert len(parts) > 1
    assert all(len(part) <= 1000 for part in parts)
    assert all(part.strip() for part in parts)


def test_paginate_short_text_is_single_message() -> None:
    assert gb.paginate("مرحباً") == ["مرحباً"]


# ── تنسيق الفحوصات والحوادث ────────────────────────────────────────
def test_format_checks_table_marks_and_names() -> None:
    results = [
        CheckResult("disk", True, "جيّد"),
        CheckResult("memory", False, "مرتفع", severity="warning"),
        CheckResult("database", False, "تالفة", severity="critical"),
    ]
    table = gb.format_checks_table(results)
    assert "✅ disk" in table
    assert "⚠️ memory" in table
    assert "🛑 database" in table
    assert "الذاكرة" in table  # التسمية العربية للفحص


def test_format_incidents_empty_and_full() -> None:
    assert "لا حوادث" in gb.format_incidents([])
    text = gb.format_incidents([{"ts": int(time.time()), "name": "disk", "action": "trim_logs"}])
    assert "disk" in text and "trim_logs" in text


# ── قراءة السجلّات ─────────────────────────────────────────────────
def test_read_log_excerpt_reads_tails_and_clamps(cfg) -> None:
    logs = cfg.target_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "bot-primary.log").write_text("\n".join(f"b{i}" for i in range(80)), encoding="utf-8")
    (logs / "guardian.log").write_text("\n".join(f"g{i}" for i in range(80)), encoding="utf-8")

    out = gb.read_log_excerpt(cfg, 5)
    assert "b79" in out and "b75" in out and "b74" not in out
    assert "g79" in out and "g75" in out
    assert "— bot-primary.log —" in out and "— guardian.log —" in out

    # الحدّ الأقصى 60 سطراً
    out_max = gb.read_log_excerpt(cfg, 999)
    assert out_max.count("\n") <= 2 * gb.MAX_LOG_LINES + 3


def test_read_log_excerpt_without_files(cfg) -> None:
    assert "لا سجلّات" in gb.read_log_excerpt(cfg, 10)


# ── حقن checks/fixers في المعالجات ─────────────────────────────────
async def test_checks_report_uses_injected_checks(cfg, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [
        CheckResult("disk", True, "جيّد"),
        CheckResult("memory", False, "مرتفع", severity="warning"),
    ]
    monkeypatch.setattr(gb.checks, "run_all", lambda _cfg: fake)
    report = await gb.checks_report(cfg)
    assert "✅ disk" in report and "⚠️ memory" in report


async def test_run_fixer_uses_injected_fixer(cfg, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[GuardianConfig] = []

    def fake_fixer(_cfg):
        calls.append(_cfg)
        return "تم الإصلاح"

    monkeypatch.setitem(gb.fixers.FIXERS, "restart_supervisor", fake_fixer)
    outcome = await gb.run_fixer("المشرف", cfg)
    assert "تم الإصلاح" in outcome
    assert calls == [cfg]


async def test_run_fixer_manual_only_is_not_executed(cfg) -> None:
    outcome = await gb.run_fixer("بوت الحارس", cfg)
    assert "تدخّل المالك" in outcome


async def test_run_fixer_unknown_returns_empty(cfg) -> None:
    assert await gb.run_fixer("لا-يوجد", cfg) == ""


# ── حاجز الكتم على الإرسال الذاتي ──────────────────────────────────
async def test_notify_admins_is_silent_while_muted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.sent: list[int] = []

        async def send_message(self, chat_id, text, parse_mode=None):  # noqa: ANN001
            self.sent.append(chat_id)

    # إعداد خفيف يحوي الحقول التي تستعملها دوال الكتم والإرسال
    light_cfg = SimpleNamespace(
        state_dir=tmp_path / "guardian" / "state", admin_chat_ids=[123]
    )
    bot = FakeBot()

    gb.set_mute(light_cfg, 30)
    assert await gb.notify_admins(bot, light_cfg, "تنبيه") is False
    assert bot.sent == []

    gb.clear_mute(light_cfg)
    assert await gb.notify_admins(bot, light_cfg, "تنبيه") is True
    assert bot.sent == [123]
