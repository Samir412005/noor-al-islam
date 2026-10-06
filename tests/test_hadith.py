"""اختبارات وكيل الحديث — بلا شبكة إطلاقاً.

تُوجَّه `hadith_api.CACHE_DIR` إلى `tmp_path`، ويُستبدل `hadith_api.http.get_json`
بدالة مزيّفة. لذلك تعمل الاختبارات بمعزل تامّ عن الإنترنت.
"""
from __future__ import annotations

import json

import pytest

from app.agents.hadith import HadithAgent
from app.models import AgentRequest, UserProfile
from app.services import hadith_api

# ── بيانات مزيّفة ───────────────────────────────────────────────────
FAKE_BOOK = {
    "metadata": {
        "name": "Sahih al Bukhari",
        "sections": {"1": "Revelation", "2": "Belief"},
    },
    "hadiths": [
        {
            "hadithnumber": 1,
            "arabicnumber": 1,
            "text": "إنما الأعمال بالنيات، وإنما لكل امرئ ما نوى.",
            "grades": [],
            "reference": {"book": 1, "hadith": 1},
        },
        {
            "hadithnumber": 2,
            "arabicnumber": 2,
            "text": "بر الوالدين من أحب الأعمال إلى الله.",
            "grades": [{"name": "Salim al-Hilali", "grade": "Sahih"}],
            "reference": {"book": 1, "hadith": 2},
        },
        {
            "hadithnumber": 7,
            "arabicnumber": 7,
            "text": "الدين النصيحة.",
            "grades": [],
            "reference": {"book": 1, "hadith": 7},
        },
    ],
}

FAKE_SECTION = {
    "metadata": {
        "name": "Sahih al Bukhari",
        "section": {"1": "Revelation"},
        "section_detail": {"1": {"hadithnumber_first": 1, "hadithnumber_last": 2}},
    },
    "hadiths": FAKE_BOOK["hadiths"][:2],
}


class FakeHTTP:
    """بديل `hadith_api.http.get_json` — يسجّل الروابط ويخدم بيانات مزيّفة."""

    def __init__(self, *, raise_on_book: bool = False) -> None:
        self.calls: list[str] = []
        self.raise_on_book = raise_on_book

    async def get_json(self, url, params=None, ttl=900):
        self.calls.append(url)
        if url.endswith("/editions.json"):
            return {key: {"collection": []} for key in hadith_api.COLLECTIONS}
        if self.raise_on_book and "/editions/ara-" in url and url.endswith(".json"):
            raise RuntimeError("no network")
        # باب واحد: .../editions/ara-{key}/{n}.json
        tail = url.rsplit("/", 1)[-1]
        if tail.endswith(".json") and tail[:-5].isdigit():
            return FAKE_SECTION
        # كتاب كامل: .../editions/ara-{key}.json
        return FAKE_BOOK


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """يعزل الكاش، ويستبدل الشبكة، ويفرّغ كاش الذاكرة بين الاختبارات."""
    monkeypatch.setattr(hadith_api, "CACHE_DIR", tmp_path)
    hadith_api.clear_cache()
    fake = FakeHTTP()
    monkeypatch.setattr(hadith_api.http, "get_json", fake.get_json)
    yield fake
    hadith_api.clear_cache()


def write_book(tmp_path, key: str = "bukhari", data: dict | None = None):
    path = tmp_path / f"ara-{key}.json"
    path.write_text(json.dumps(data or FAKE_BOOK, ensure_ascii=False), encoding="utf-8")
    return path


def make_request(text: str = "", args: str = "") -> AgentRequest:
    user = UserProfile(chat_id=42, first_name="سمير")
    return AgentRequest(text=text, chat_id=42, user=user, args=args)


# ── find_collection ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "query,expected",
    [
        ("البخاري", "bukhari"),
        ("بخاري", "bukhari"),
        ("صحيح البخاري", "bukhari"),
        ("مسلم", "muslim"),
        ("صحيح مسلم", "muslim"),
        ("الترمذي", "tirmidhi"),
        ("جامع الترمذي", "tirmidhi"),
        ("النسائي", "nasai"),
        ("ابن ماجه", "ibnmajah"),
        ("أبو داود", "abudawud"),
        ("ابو داود", "abudawud"),
        ("مالك", "malik"),
        ("الموطأ", "malik"),
        ("النووية", "nawawi"),
        ("الأربعون النووية", "nawawi"),
        ("قدسي", "qudsi"),
        ("الأحاديث القدسية", "qudsi"),
        ("bukhari", "bukhari"),
        ("Muslim", "muslim"),
    ],
)
def test_find_collection(query, expected):
    assert hadith_api.find_collection(query) == expected


def test_find_collection_unknown():
    assert hadith_api.find_collection("حديث اليوم") is None
    assert hadith_api.find_collection("") is None
    assert hadith_api.find_collection("كلام عام") is None


def test_find_collection_in_sentence():
    assert hadith_api.find_collection("أعطني حديثاً من صحيح مسلم") == "muslim"
    assert hadith_api.find_collection("البخاري 1") == "bukhari"


# ── load_book ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_load_book_reads_from_disk_without_network(fake_env, tmp_path):
    write_book(tmp_path, "bukhari")

    async def boom(*a, **k):
        raise AssertionError("يجب ألّا تُلمس الشبكة عند وجود الكتاب على القرص")

    hadith_api.http.get_json = boom  # type: ignore[assignment]
    book = await hadith_api.load_book("bukhari")
    assert len(book["hadiths"]) == 3
    assert book["metadata"]["sections"]["1"] == "Revelation"


@pytest.mark.asyncio
async def test_load_book_downloads_and_caches(fake_env, tmp_path):
    book = await hadith_api.load_book("bukhari")
    assert len(book["hadiths"]) == 3
    cache_file = tmp_path / "ara-bukhari.json"
    assert cache_file.exists()
    assert any("ara-bukhari.json" in url for url in fake_env.calls)

    # الطلب الثاني يقرأ من القرص: نمنع الشبكة.
    hadith_api.clear_cache()

    async def boom(*a, **k):
        raise AssertionError("لا شبكة بعد التخزين على القرص")

    hadith_api.http.get_json = boom  # type: ignore[assignment]
    again = await hadith_api.load_book("bukhari")
    assert again["hadiths"][0]["text"] == FAKE_BOOK["hadiths"][0]["text"]


@pytest.mark.asyncio
async def test_load_book_unknown_key(fake_env):
    with pytest.raises(ValueError):
        await hadith_api.load_book("dehlawi")


# ── sections / get_hadith ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_sections(fake_env):
    result = await hadith_api.sections("bukhari")
    assert result == {"1": "Revelation", "2": "Belief"}


@pytest.mark.asyncio
async def test_get_hadith_by_number(fake_env):
    hadith = await hadith_api.get_hadith("bukhari", 2)
    assert hadith is not None
    assert hadith["number"] == 2
    assert "بر الوالدين" in hadith["text"]
    assert hadith["collection"] == "صحيح البخاري"
    assert hadith["book"] == 1
    assert hadith["hadith_ref"] == 2
    assert hadith["grades"][0]["grade"] == "Sahih"


@pytest.mark.asyncio
async def test_get_hadith_missing_returns_none(fake_env):
    assert await hadith_api.get_hadith("bukhari", 9999) is None


@pytest.mark.asyncio
async def test_get_hadith_normalizes_unknown_collection(fake_env):
    hadith = await hadith_api.get_hadith("البخاري", 1)
    assert hadith is not None and hadith["key"] == "bukhari"


# ── search_hadith ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_search_hadith_single_word(fake_env):
    results = await hadith_api.search_hadith("الوالدين", key="bukhari")
    assert len(results) == 1
    assert results[0]["number"] == 2


@pytest.mark.asyncio
async def test_search_hadith_multi_word(fake_env):
    results = await hadith_api.search_hadith("بر الوالدين", key="bukhari")
    assert [item["number"] for item in results] == [2]


@pytest.mark.asyncio
async def test_search_hadith_ignores_diacritics(fake_env):
    results = await hadith_api.search_hadith("الأَعْمَال", key="bukhari")
    numbers = {item["number"] for item in results}
    assert 1 in numbers and 2 in numbers


@pytest.mark.asyncio
async def test_search_hadith_no_results(fake_env):
    assert await hadith_api.search_hadith("زريقة", key="bukhari") == []


@pytest.mark.asyncio
async def test_search_hadith_default_scans_core_books(fake_env):
    results = await hadith_api.search_hadith("الوالدين", limit=5)
    assert results
    # كل كتب النواة المزيّفة تحمل الحديث نفسه ⇒ مفتاح مختلف لكل نتيجة.
    assert len({item["key"] for item in results}) >= 2


# ── random_hadith ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_random_hadith_seed_is_stable(fake_env):
    first = await hadith_api.random_hadith("bukhari", seed=20260101)
    second = await hadith_api.random_hadith("bukhari", seed=20260101)
    assert first["number"] == second["number"]
    assert first["text"]


@pytest.mark.asyncio
async def test_random_hadith_picks_core_collection(fake_env):
    hadith = await hadith_api.random_hadith(seed=7)
    assert hadith["key"] in hadith_api.CORE_COLLECTIONS


# ── book_section ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_book_section(fake_env):
    section = await hadith_api.book_section("bukhari", 1)
    assert section["key"] == "bukhari"
    assert section["collection"] == "صحيح البخاري"
    assert section["title"] == "Revelation"
    assert [h["number"] for h in section["hadiths"]] == [1, 2]
    assert any(url.endswith("ara-bukhari/1.json") for url in fake_env.calls)


# ── reference_text ─────────────────────────────────────────────────
def test_reference_text_uses_arabic_digits():
    text = hadith_api.reference_text({"collection": "صحيح البخاري", "number": 1})
    assert text == "صحيح البخاري — حديث رقم ١"
    assert "1" not in text  # لا أرقام لاتينية


def test_collections_map_complete():
    assert hadith_api.COLLECTIONS["bukhari"] == "صحيح البخاري"
    assert len(hadith_api.COLLECTIONS) == 9


# ── الوكيل ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_agent_daily_works_offline(fake_env):
    reply = await HadithAgent().handle(make_request("حديث اليوم"))
    assert "حديث اليوم" in reply.text
    assert "📜" in reply.text
    assert "المصدر" in reply.text
    assert reply.sources == ["fawazahmed0/hadith-api (النصوص العربية)"]
    assert reply.buttons


@pytest.mark.asyncio
async def test_agent_bukhari_1_shows_full_text(fake_env):
    reply = await HadithAgent().handle(make_request("البخاري 1"))
    assert "إنما الأعمال بالنيات" in reply.text
    assert "حديث ١" in reply.text
    assert "المصدر" in reply.text


@pytest.mark.asyncio
async def test_agent_muslim_reference(fake_env):
    reply = await HadithAgent().handle(make_request("صحيح مسلم حديث 1"))
    assert "إنما الأعمال بالنيات" in reply.text


@pytest.mark.asyncio
async def test_agent_search(fake_env):
    reply = await HadithAgent().handle(make_request("ابحث عن بر الوالدين"))
    assert "بر الوالدين" in reply.text
    assert reply.buttons  # زر التفاصيل لكل نتيجة


@pytest.mark.asyncio
async def test_agent_search_no_results(fake_env):
    reply = await HadithAgent().handle(make_request("ابحث عن زريقة"))
    assert "لم أجد" in reply.text
    assert reply.buttons


@pytest.mark.asyncio
async def test_agent_callbacks(fake_env):
    agent = HadithAgent()
    detail = await agent.handle(make_request(args="ag:hadith:get:bukhari:2"))
    assert "بر الوالدين" in detail.text

    daily = await agent.handle(make_request(args="ag:hadith:random"))
    assert "📜" in daily.text

    section = await agent.handle(make_request(args="ag:hadith:section:bukhari:1"))
    assert "باب" in section.text


@pytest.mark.asyncio
async def test_agent_handles_network_failure_gracefully(fake_env):
    hadith_api.clear_cache()

    async def boom(*a, **k):
        raise RuntimeError("offline")

    hadith_api.http.get_json = boom  # type: ignore[assignment]
    reply = await HadithAgent().handle(make_request("حديث اليوم"))
    assert "تعذّر" in reply.text
    assert reply.agent == "hadith"


@pytest.mark.asyncio
async def test_agent_html_is_escaped(fake_env):
    data = json.loads(json.dumps(FAKE_BOOK))
    data["hadiths"][2]["text"] = "<script>alert(1)</script> ومتنٌ فيه <b>وسم</b>"
    write_book(hadith_api.CACHE_DIR, "bukhari", data)
    hadith_api.clear_cache()

    async def boom(*a, **k):
        raise AssertionError("لا شبكة: الكتاب على القرص")

    hadith_api.http.get_json = boom  # type: ignore[assignment]
    reply = await HadithAgent().handle(make_request("البخاري 7"))
    assert "&lt;script&gt;" in reply.text
    assert "<script>" not in reply.text


def test_callback_data_limit_for_long_query():
    """حدّ تيليجرام: 64 بايت — والقصّ يجب أن يبقى بلا حالة في الذاكرة."""
    from app.agents import hadith as hadith_module

    agent = HadithAgent()
    long_query = "بر الوالدين " * 12
    data = hadith_module._callback("search", long_query.strip())  # noqa: SLF001
    assert len(data.encode("utf-8")) <= 64
    assert data.startswith("ag:hadith:search:")
    query = data.split(":", 3)[3]
    assert query and long_query.strip().startswith(query)
    assert agent.name == "hadith"


def test_random_button_respects_collection():
    """زرّ «📚 الكتاب» يجب أن يطلب من الكتاب نفسه لا عشوائياً من غيره."""
    import asyncio

    from app.agents import hadith as hadith_module
    from app.models import UserProfile

    captured: dict[str, str | None] = {}

    async def fake_random(key=None, *, seed=None):
        captured["key"] = key
        return {
            "key": key or "bukhari",
            "collection": "صحيح البخاري",
            "number": 1,
            "text": "نصّ",
            "grades": [],
            "book": 1,
            "hadith_ref": 1,
        }

    agent = HadithAgent()
    original = hadith_module.hadith_api.random_hadith
    hadith_module.hadith_api.random_hadith = fake_random
    try:
        request = AgentRequest(
            text="", args="ag:hadith:random:muslim", chat_id=1, user=UserProfile(chat_id=1)
        )
        reply = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            agent.handle(request)
        )
    finally:
        hadith_module.hadith_api.random_hadith = original
    assert captured["key"] == "muslim"
    assert reply.text


def test_agent_metadata():
    agent = HadithAgent()
    assert agent.name == "hadith"
    assert agent.priority == 85
    assert "حديث" in agent.keywords
    assert agent.examples
