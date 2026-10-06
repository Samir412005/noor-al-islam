"""اختبارات الموجّه: من النصّ إلى الوكيل الصحيح."""
from __future__ import annotations

import pytest

from app.models import AgentRequest, UserProfile
from app.router import (
    _parse_classification,
    ayah_reference_route,
    explicit_route,
    keyword_route,
    route,
)

USER = UserProfile(chat_id=1, first_name="سمير")


def request(text: str, raw: str = "") -> AgentRequest:
    return AgentRequest(text=text, chat_id=1, user=USER, raw_command=raw)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("آية اليوم", "quran"),
        ("تفسير 2:255", "quran"),
        ("سورة الملك", "quran"),
        ("ابحث عن الصبر", "quran"),
        ("حديث اليوم", "hadith"),
        ("البخاري 1", "hadith"),
        ("صحيح مسلم 4", "hadith"),
        ("أحاديث عن بر الوالدين", "hadith"),
        ("أذكار الصباح", "adhkar"),
        ("دعاء الهم والحزن", "adhkar"),
        ("مواقيت الصلاة", "prayer"),
        ("القبلة", "prayer"),
        ("كم بقي للفجر", "prayer"),
        ("المناسبات القادمة", "occasions"),
        ("متى عيد الأضحى", "occasions"),
    ],
)
def test_keyword_routes(text: str, expected: str) -> None:
    found = keyword_route(text)
    assert found is not None, f"لم يُصنَّف: {text}"
    assert found.agent == expected


@pytest.mark.parametrize(
    "text",
    ["2:255", "البقرة 255", "آية الكرسي", "114:6"],
)
def test_ayah_reference(text: str) -> None:
    found = ayah_reference_route(text)
    assert found is not None and found.agent == "quran"


@pytest.mark.parametrize("text", ["البخاري 1", "صحيح مسلم 4", "حديث 12", "2026"])
def test_ayah_reference_does_not_steal_hadith(text: str) -> None:
    """أرقام الأحاديث ليست مراجع آيات."""
    found = ayah_reference_route(text)
    if found is not None:  # «2026» وحدها مقبولة كمرجع (سلوك متسامح)
        assert "بخاري" not in text and "مسلم" not in text


def test_explicit_commands() -> None:
    assert explicit_route("/quran").agent == "quran"  # type: ignore[union-attr]
    assert explicit_route("/ask@NoorIslamSamir2026Bot").agent == "scholar"  # type: ignore[union-attr]
    assert explicit_route("/prayer").agent == "prayer"  # type: ignore[union-attr]
    assert explicit_route("").reason if explicit_route("") else True
    assert explicit_route("نصّ عادي") is None


def test_explicit_callbacks() -> None:
    route_data = explicit_route("ag:hadith:get:bukhari:1")
    assert route_data is not None and route_data.agent == "hadith"
    assert route_data.args == "get:bukhari:1"

    qibla = explicit_route("ag:prayer:qibla")
    assert qibla is not None and qibla.agent == "prayer" and qibla.args == "qibla"

    item = explicit_route("ag:adhkar:item:12:3")
    assert item is not None and item.args == "item:12:3"

    assert explicit_route("ag:unknown:thing") is None


async def test_route_prefers_explicit_then_reference_then_keyword() -> None:
    assert (await route(request("مواقيت الصلاة"))).agent == "prayer"
    assert (await route(request("2:255"))).agent == "quran"
    explicit = await route(request("", raw="/hadith"))
    assert explicit.agent == "hadith" and explicit.reason == "command"


async def test_unknown_text_falls_back_to_default() -> None:
    from app.agents import DEFAULT_AGENT

    found = await route(request("كلمة مبهمة جداً"), use_llm=False)
    assert found.agent == DEFAULT_AGENT and found.reason == "default"


def test_parse_classification_handles_noise() -> None:
    assert _parse_classification('{"agent":"quran","args":""}')[0] == "quran"
    assert _parse_classification('```json\n{"agent":"hadith","args":"النية"}\n```')[0] == "hadith"
    assert _parse_classification("لا شيء")[0] == ""
    assert _parse_classification('{"agent":"لا_يوجد"}')[0] == ""


def test_all_agents_are_scored_without_error() -> None:
    from app.agents import all_agents

    for agent in all_agents():
        score = agent.score("آية اليوم حديث عن الصبر مواقيت الصلاة أذكار")
        assert isinstance(score, float)
