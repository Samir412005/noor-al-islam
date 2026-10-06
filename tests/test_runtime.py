"""اختبارات الطبقة الحيّة: فحص الصحّة، الإشعارات الدائمة، كشف مفاتيح المزوّدين."""
from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.config import Settings
from app.models import AgentReply


# ── الإعدادات: كشف تلقائي لمفاتيح المزوّدين ────────────────────────
@pytest.mark.parametrize(
    ("env_key", "expected_base"),
    [
        ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
        ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
        ("OPENAI_API_KEY", "https://api.openai.com/v1"),
        ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ],
)
def test_provider_keys_are_auto_detected(
    monkeypatch: pytest.MonkeyPatch, env_key: str, expected_base: str
) -> None:
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "GROQ_API_KEY",
                "OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(env_key, "test-key-123456")

    settings = Settings()
    base, key, model = settings.resolved_llm
    assert base == expected_base
    assert key == "test-key-123456" and model
    assert settings.llm_enabled is True


def test_explicit_settings_win_over_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    monkeypatch.setenv("LLM_BASE_URL", "https://explicit.example/v1")
    monkeypatch.setenv("LLM_MODEL", "my-model")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    settings = Settings()
    base, key, model = settings.resolved_llm
    assert base == "https://explicit.example/v1" and model == "my-model"
    assert key == ""  # بلا مفتاح ⇒ غير مفعّل (إلا للنماذج المحلية)
    assert settings.llm_enabled is False


def test_llm_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "GROQ_API_KEY",
                "OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert Settings().llm_enabled is False


# ── نقطة الفحص الصحّي (تُستعمل من الحارس خارجياً) ───────────────────
async def test_health_endpoint_responds(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiogram import Bot

    from app import main as main_module

    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(),  # إعداد نظيف بلا حاجة لتوكن حقيقي
    )
    bot = Bot(token="123456:AAfake-token-for-tests", validate_token=False)
    from app.bot import create_dispatcher

    app = main_module.build_web_app(create_dispatcher(), bot)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/health")
        assert response.status == 200
        payload = await response.json()
        assert payload["status"] == "ok" and payload["service"] == "noor-islam-bot"

        # نقطة الويب هوك موجودة وترفض ما ليس من تيليجرام
        rejected = await client.post("/telegram/webhook", json={"update_id": 1})
        assert rejected.status in (401, 403)

    await bot.session.close()


# ── حلقة الإشعارات تعمل دائماً (القرار لكل مستخدم) ─────────────────
async def test_scheduler_loop_always_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """يعمل دائماً — حتى مع DAILY_PUSH=false — وإلا لما وصل إشعارٌ لمن فعّله بنفسه."""
    from app import scheduler

    assert scheduler.settings.daily_push is False  # الافتراضي في الاختبارات
    calls: list[int] = []

    async def fake_send(bot):
        calls.append(1)
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "_send_daily", fake_send)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_scheduler(object())  # type: ignore[arg-type]
    assert len(calls) == 1


async def test_daily_payload_builder_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """يبني كل إشعار من الوكيل المناسب (لا منطق مكرّر)."""
    from app import scheduler

    async def fake_process(request):
        return AgentReply(text=f"محتوى {request.text}", agent="test")

    monkeypatch.setattr(scheduler, "process", fake_process)
    from app.models import UserProfile

    user = UserProfile(chat_id=1, daily_push=True)
    for kind in ("quran", "adhkar_morning", "adhkar_evening", "prayer"):
        reply = await scheduler._payload(kind, user)
        assert reply is not None and reply.text.startswith("محتوى")


# ── الحارس: فحص العنوان العامّ ─────────────────────────────────────
def test_watchdog_public_health(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from scripts import watchdog  # type: ignore[import-not-found]

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    monkeypatch.setattr(watchdog.httpx, "get", lambda *a, **k: FakeResponse())
    ok, detail = watchdog.public_health("https://example.com")
    assert ok and "تستجيب" in detail

    def boom(*args, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(watchdog.httpx, "get", boom)
    ok, detail = watchdog.public_health("https://example.com")
    assert not ok and "لا يستجيب" in detail

    ok, detail = watchdog.public_health("")
    assert not ok and "غير مضبوط" in detail
