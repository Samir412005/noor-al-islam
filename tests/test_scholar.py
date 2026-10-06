"""اختبارات وكيل العلم (بلا شبكة وبلا LLM): البحث متعدّد المصادر والحواجز."""
from __future__ import annotations

import pytest

from app.agents.scholar import GREETINGS, ScholarAgent, _query_terms
from app.models import AgentRequest, UserProfile

USER = UserProfile(chat_id=3, first_name="سمير")
AGENT = ScholarAgent()


def req(text: str, args: str = "") -> AgentRequest:
    return AgentRequest(text=text, args=args, chat_id=3, user=USER)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ما معنى الصبر في القرآن؟", "الصبر"),
        ("اذكر لي أحاديث عن النية", "لي أحاديث عن النية"),
        ("أخبرني عن بر الوالدين", "بر الوالدين"),
        ("   ", ""),
    ],
)
def test_query_terms_strips_question_words(raw: str, expected: str) -> None:
    assert _query_terms(raw) == expected


async def test_greeting_returns_welcome() -> None:
    reply = await AGENT.handle(req("السلام عليكم"))
    assert "وعليكم السلام" in reply.text
    assert reply.buttons


@pytest.mark.parametrize("greeting", list(GREETINGS)[:5])
async def test_all_greetings_recognised(greeting: str) -> None:
    reply = await AGENT.handle(req(greeting))
    assert "ورحمة الله" in reply.text or "نور الإسلام" in reply.text


async def test_fatwa_question_answered_with_referral() -> None:
    reply = await AGENT.handle(req("ما حكم الربا؟"))
    assert "الفتوى" in reply.text or "لا يُفتي" in reply.text or "فتوى" in reply.text
    assert reply.sources


async def test_crisis_question_returns_help_not_search() -> None:
    reply = await AGENT.handle(req("أريد أن أنهي حياتي"))
    assert "الطوارئ" in reply.text


async def test_offline_answer_uses_sources_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """بدون LLM: يجمع من القرآن والحديث والأذكار ويعرضها بمصادرها."""
    from app.agents import adhkar as adhkar_agent
    from app.services import hadith_api, quran_api

    monkeypatch.setattr(
        quran_api,
        "search_ayahs",
        lambda query, limit=3: [
            {"surah": 2, "surah_name": "البقرة", "ayah": 153, "text": "يَا أَيُّهَا الَّذِينَ آمَنُوا"}
        ],
    )
    monkeypatch.setattr(hadith_api, "search_hadith", _fake_hadith_search)
    monkeypatch.setattr(
        adhkar_agent,
        "search_adhkar",
        lambda query, limit=3: [
            {"category": {"id": 1, "title": "أذكار الصباح والمساء"}, "item": {"text": "سبحان الله"}, "index": 1}
        ],
    )

    reply = await AGENT._offline_answer("ما معنى الصبر؟")
    assert reply.meta.get("offline") is True
    assert "من القرآن" in reply.text
    assert "من السنة" in reply.text
    assert "من الأذكار" in reply.text
    assert len(reply.sources) == 3


async def test_offline_answer_says_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents import adhkar as adhkar_agent
    from app.services import hadith_api, quran_api

    monkeypatch.setattr(quran_api, "search_ayahs", lambda query, limit=3: [])
    monkeypatch.setattr(hadith_api, "search_hadith", _empty_hadith_search)
    monkeypatch.setattr(adhkar_agent, "search_adhkar", lambda query, limit=3: [])

    reply = await AGENT._offline_answer("سؤال غريب جداً عن كذا")
    assert "لم أجد" in reply.text


async def test_offline_answer_survives_service_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """فشل خدمة لا يجب أن يُسقط الوكيل."""
    from app.agents import adhkar as adhkar_agent
    from app.services import hadith_api, quran_api

    def boom(*args, **kwargs):
        raise RuntimeError("الشبكة معطّلة")

    monkeypatch.setattr(quran_api, "search_ayahs", boom)
    monkeypatch.setattr(hadith_api, "search_hadith", _boom_async)
    monkeypatch.setattr(adhkar_agent, "search_adhkar", boom)

    reply = await AGENT._offline_answer("سؤال ما")
    assert reply.text and "لم أجد" in reply.text


def test_tools_declared_and_implemented() -> None:
    schemas = ScholarAgent._tool_schemas()
    names = {schema["function"]["name"] for schema in schemas}
    assert names == {
        "quran_search",
        "quran_ayah",
        "quran_tafsir",
        "hadith_search",
        "hadith_get",
        "adhkar_search",
        "prayer_times",
        "hijri_date",
    }
    collected: dict = {"sources": [], "used": False}
    impls = AGENT._tool_impls(req("سؤال"), collected)
    assert set(impls) == names


async def _fake_hadith_search(query, key=None, limit=4):
    return [
        {
            "key": "bukhari",
            "collection": "صحيح البخاري",
            "number": 1,
            "text": "إنما الأعمال بالنيات",
            "grades": [],
            "book": 1,
            "hadith_ref": 1,
        }
    ]


async def _empty_hadith_search(query, key=None, limit=4):
    return []


async def _boom_async(*args, **kwargs):
    raise RuntimeError("الشبكة معطّلة")
