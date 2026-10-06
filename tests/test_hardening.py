"""اختبارات الإصلاحات الأمنية والتكاملية (قفل الانحدار)."""
from __future__ import annotations

import dataclasses

import pytest

from app.config import settings
from app.models import AgentRequest, UserProfile

USER = UserProfile(chat_id=5, first_name="سمير", reciter="ar.husary")


def test_webhook_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """بدون سرّ، يستطيع أي طرف تزوير تحديثات تيليجرام ⇒ يجب أن يرفض الإقلاع."""
    import app.main as main

    insecure = dataclasses.replace(
        settings, run_mode="webhook", webhook_base_url="https://example.com", webhook_secret=""
    )
    monkeypatch.setattr(main, "settings", insecure)
    with pytest.raises(SystemExit):
        main.validate_settings()


def test_webhook_with_secret_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main

    secure = dataclasses.replace(
        settings,
        run_mode="webhook",
        webhook_base_url="https://example.com",
        webhook_secret="a-very-long-random-string",
        bot_token="123456:AAfake",
    )
    monkeypatch.setattr(main, "settings", secure)
    main.validate_settings()  # لا يرفع


def test_invalid_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main

    broken = dataclasses.replace(settings, bot_token="not-a-token")
    monkeypatch.setattr(main, "settings", broken)
    with pytest.raises(SystemExit):
        main.validate_settings()


async def test_quran_audio_uses_user_reciter() -> None:
    """القارئ المختار في الإعدادات يجب أن يُحترم في التلاوة."""
    from app.agents import get_agent

    agent = get_agent("quran")
    assert agent is not None
    reply = await agent.handle(
        AgentRequest(text="تلاوة 1:1", chat_id=5, user=USER)
    )
    assert reply.audio_url and "ar.husary" in reply.audio_url


async def test_scholar_drops_unsourced_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """الاختبار الحاسم: النموذج يُضيف حديثاً مكذوباً فيُحذف من الجواب."""
    from app.agents import scholar as scholar_module
    from app.db import Database
    from app.llm import llm

    test_db = Database(":memory:")
    await test_db.connect()
    monkeypatch.setattr(scholar_module, "db", test_db)
    monkeypatch.setattr(type(llm), "enabled", property(lambda self: True))

    fabricated = (
        "النيات أصل الأعمال.\n"
        "قال رسول الله ﷺ: «إنما الأعمال بالنيات» رواه البخاري\n"
        "قال رسول الله ﷺ: «من نام بعد العصر فاختُلس عقله فلا يلومنّ إلا نفسه»"
    )

    async def fake_chat(messages, *, tools=None, tool_impls=None, **kwargs):
        on_result = kwargs.get("on_tool_result")
        if on_result:
            on_result("hadith_search", "النيات: عن عمر بن الخطاب قال: إنما الأعمال بالنيات — صحيح البخاري")
        return fabricated

    monkeypatch.setattr(llm, "chat", fake_chat)

    reply = await scholar_module.ScholarAgent().handle(
        AgentRequest(text="ما معنى حديث إنما الأعمال بالنيات؟", chat_id=5, user=USER)
    )
    assert "إنما الأعمال بالنيات" in reply.text
    assert "اختُلس عقله" not in reply.text  # النصّ المُفترى حُذف
    assert "حُذف" in reply.text
    await test_db.close()


async def test_scholar_keeps_verified_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents import scholar as scholar_module
    from app.db import Database
    from app.llm import llm

    test_db = Database(":memory:")
    await test_db.connect()
    monkeypatch.setattr(scholar_module, "db", test_db)
    monkeypatch.setattr(type(llm), "enabled", property(lambda self: True))

    async def fake_chat(messages, *, tools=None, tool_impls=None, **kwargs):
        on_result = kwargs.get("on_tool_result")
        if on_result:
            on_result("quran_search", "[البقرة: 153] يا أيها الذين آمنوا استعينوا بالصبر والصلاة")
        return "استعن بالصبر: ﴿يَا أَيُّهَا الَّذِينَ آمَنُوا اسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ﴾ — وتأمّل موضع التقديم."

    monkeypatch.setattr(llm, "chat", fake_chat)

    reply = await scholar_module.ScholarAgent().handle(
        AgentRequest(text="ما معنى الصبر؟", chat_id=5, user=USER)
    )
    from app.text import normalize_arabic

    assert "حُذف" not in reply.text
    assert "المرجوّ التحقّق" not in reply.text  # لا تحذير زائف عند وجود مصادر
    # التطبيع يحوّل «ة» إلى «ه»
    assert "والصلاه" in normalize_arabic(reply.text)
    await test_db.close()


def test_verify_quotes_direct() -> None:
    from app.guardrails import verify_quotes

    corpus = "قال رسول الله ﷺ: «إنما الأعمال بالنيات» رواه البخاري"
    text = "أولاً.\nقال رسول الله ﷺ: «إنما الأعمال بالنيات»\nقال ﷺ: «الجنة تحت أقدام الأمهات»"
    clean, warnings = verify_quotes(text, corpus)
    assert warnings == ["unverified_quotes"]
    assert "إنما الأعمال بالنيات" in clean
    assert "أقدام الأمهات" not in clean
