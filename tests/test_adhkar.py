"""اختبارات وكيل الأذكار — بلا شبكة.

نوجّه مسار البيانات إلى ملف مؤقّت (fixture) ونستبدل قاعدة البيانات بنسخة
``:memory:``، ثم نختبر الدوال النقية والوكيل.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.agents import adhkar
from app.agents.adhkar import AdhkarAgent
from app.db import Database
from app.models import AgentRequest, UserProfile

# ── بيانات اختبار مصغّرة (نفس بنية الملف المنتج) ────────────────────
FIXTURE = {
    "source": "اختبار",
    "audio_base": "https://cdn.example/base",
    "categories": [
        {
            "id": 1,
            "title": "أذكار الصباح والمساء",
            "audio": "https://cdn.example/base/audio/door1.mp3",
            "tags": ["صباح", "مساء"],
            "items": [
                {
                    "id": 1,
                    "text": "سبحان الله وبحمده",
                    "count": 3,
                    "audio": "https://cdn.example/base/audio/a.mp3",
                },
                {"id": 2, "text": "لا إله إلا الله وحده لا شريك له", "count": 1, "audio": ""},
            ],
        },
        {
            "id": 2,
            "title": "أذكار النوم",
            "audio": "",
            "tags": ["نوم"],
            "items": [
                {
                    "id": 1,
                    "text": "باسمك اللهم أموت وأحيا",
                    "count": 1,
                    "audio": "https://cdn.example/base/audio/b.mp3",
                }
            ],
        },
        {
            "id": 34,
            "title": "دعاء الهم والحزن",
            "audio": "",
            "tags": ["هم"],
            "items": [{"id": 1, "text": "اللهم إني عبدك ابن عبدك", "count": 1, "audio": ""}],
        },
        {
            "id": 96,
            "title": "دعاء السفر",
            "audio": "",
            "tags": ["سفر"],
            "items": [
                {"id": 1, "text": "سبحان الذي سخر لنا هذا", "count": 1, "audio": ""}
            ],
        },
    ],
}


@pytest.fixture
def fixture_data(tmp_path, monkeypatch):
    """يوجّه ``DATA_PATH`` إلى ملف مؤقّت ويصفّر الذاكرة المؤقتة."""
    path = tmp_path / "adhkar.json"
    path.write_text(json.dumps(FIXTURE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(adhkar, "DATA_PATH", path)
    adhkar.reset_cache()
    yield path
    adhkar.reset_cache()


@pytest_asyncio.fixture
async def memory_db():
    database = Database(path=":memory:")
    await database.connect()
    yield database
    await database.close()


def make_request(text: str = "", args: str = "", chat_id: int = 555) -> AgentRequest:
    return AgentRequest(text=text, chat_id=chat_id, user=UserProfile(chat_id=chat_id), args=args)


# ── الدوال النقية ───────────────────────────────────────────────────
def test_load_data(fixture_data):
    data = adhkar.load_data()
    assert len(data["categories"]) == 4
    assert sum(len(c["items"]) for c in data["categories"]) == 5
    assert data["audio_base"] == "https://cdn.example/base"
    first = data["categories"][0]
    assert first["tags"] == ["صباح", "مساء"]
    assert first["items"][0]["audio"].startswith("https://")


def test_load_data_explicit_path(tmp_path):
    adhkar.reset_cache()
    path = tmp_path / "explicit.json"
    path.write_text(json.dumps(FIXTURE, ensure_ascii=False), encoding="utf-8")
    data = adhkar.load_data(path)
    assert len(data["categories"]) == 4
    adhkar.reset_cache()

    with pytest.raises(FileNotFoundError):
        adhkar.load_data(tmp_path / "missing.json")
    adhkar.reset_cache()


def test_find_category(fixture_data):
    assert adhkar.find_category("الصباح")["id"] == 1
    assert adhkar.find_category("أذكار النوم")["id"] == 2
    assert adhkar.find_category("السفر")["id"] == 96
    assert adhkar.find_category("") is None


def test_find_categories(fixture_data):
    cats = adhkar.find_categories("الصباح", limit=8)
    assert [c["id"] for c in cats] == [1]
    assert adhkar.find_categories("النوم")[0]["id"] == 2


def test_render_item(fixture_data):
    text = adhkar.render_item(1, 1)
    assert text is not None
    assert "سبحان الله وبحمده" in text
    assert "يُقال ٣ مرات" in text
    assert "المصدر: حصن المسلم" in text
    assert "الذكر ١ من ٢" in text
    # خارج الحدود
    assert adhkar.render_item(1, 99) is None
    assert adhkar.render_item(999, 1) is None


def test_search_adhkar(fixture_data):
    results = adhkar.search_adhkar("سبحان", limit=5)
    assert results
    assert results[0]["category"]["id"] == 1
    assert results[0]["item"]["text"] == "سبحان الله وبحمده"
    assert adhkar.search_adhkar("ززز") == []


@pytest.mark.skipif(not adhkar.data_available(), reason="لم يُشغَّل سكربت الجلب بعد")
def test_real_dataset_generated():
    adhkar.reset_cache()
    data = adhkar.load_data()
    adhkar.reset_cache()
    assert len(data["categories"]) >= 100
    assert sum(len(c["items"]) for c in data["categories"]) >= 200
    cat = next(c for c in data["categories"] if c["id"] == 1)
    assert cat["tags"]


# ── الوكيل ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_show_item_and_navigation(fixture_data, memory_db, monkeypatch):
    monkeypatch.setattr(adhkar, "db", memory_db)
    agent = AdhkarAgent()

    reply = await agent.handle(make_request("أذكار الصباح"))
    assert reply.agent == "adhkar"
    assert "سبحان الله وبحمده" in reply.text
    assert reply.meta["category_id"] == 1 and reply.meta["index"] == 1
    assert reply.audio_url is None  # لا صوت إلا عند الطلب
    assert any("التالي" in b.text for row in reply.buttons for b in row)

    # التالي ضمن الحدود
    nxt = await agent.handle(make_request(args="ag:adhkar:next:1:1"))
    assert nxt.meta["index"] == 2
    assert "لا إله إلا الله" in nxt.text

    # التالي بعد الحد ⇒ يثبت عند العنصر الأخير
    clamped = await agent.handle(make_request(args="ag:adhkar:next:1:2"))
    assert clamped.meta["index"] == 2

    # السابق قبل الأول ⇒ يثبت عند 1
    prev = await agent.handle(make_request(args="ag:adhkar:prev:1:1"))
    assert prev.meta["index"] == 1

    # زر التنقّل يعيد دائماً ضمن [1, total]
    assert 1 <= prev.meta["index"] <= prev.meta["total"]


@pytest.mark.asyncio
async def test_category_page(fixture_data):
    agent = AdhkarAgent()
    reply = await agent.handle(make_request(args="ag:adhkar:cat:1"))
    assert "أذكار الصباح والمساء" in reply.text
    datas = [b.data for row in reply.buttons for b in row]
    assert "ag:adhkar:item:1:1" in datas
    assert "ag:adhkar:item:1:2" in datas


@pytest.mark.asyncio
async def test_audio_requested_only(fixture_data, memory_db, monkeypatch):
    monkeypatch.setattr(adhkar, "db", memory_db)
    agent = AdhkarAgent()
    reply = await agent.handle(make_request(args="ag:adhkar:audio:1:1"))
    assert reply.audio_url == "https://cdn.example/base/audio/a.mp3"
    assert reply.audio_title == "أذكار الصباح والمساء"

    # ذكر بلا صوت
    silent = await agent.handle(make_request(args="ag:adhkar:audio:1:2"))
    assert silent.audio_url is None


@pytest.mark.asyncio
async def test_tasbih_counter(fixture_data, memory_db, monkeypatch):
    monkeypatch.setattr(adhkar, "db", memory_db)
    agent = AdhkarAgent()

    first = await agent.handle(make_request("تسبيح"))
    assert first.meta["tasbih"] == 1
    assert "١" in first.text
    assert any("سبّح" in b.text for row in first.buttons for b in row)

    second = await agent.handle(make_request(args="ag:adhkar:t_tasbih"))
    assert second.meta["tasbih"] == 2
    assert await memory_db.get_counter(555, "tasbih") == 2

    other_user = await agent.handle(make_request("سبحان الله", chat_id=777))
    assert other_user.meta["tasbih"] == 1  # عدّاد مستقل لكل مستخدم

    reset = await agent.handle(make_request(args="ag:adhkar:reset"))
    assert reset.meta["tasbih"] == 0
    assert await memory_db.get_counter(555, "tasbih") == 0


@pytest.mark.asyncio
async def test_random_and_search(fixture_data):
    agent = AdhkarAgent()

    random_reply = await agent.handle(make_request("ذكرني"))
    assert random_reply.meta["category_id"] in {1, 2, 34, 96}

    search_reply = await agent.handle(make_request("ابحث عن سبحان الله"))
    assert "نتائج البحث" in search_reply.text
    assert "سبحان الله وبحمده" in search_reply.text


@pytest.mark.asyncio
async def test_dua_menu(fixture_data):
    agent = AdhkarAgent()
    reply = await agent.handle(make_request("دعاء"))
    datas = [b.data for row in reply.buttons for b in row]
    assert "ag:adhkar:cat:34" in datas


@pytest.mark.asyncio
async def test_missing_data_message(tmp_path, monkeypatch):
    monkeypatch.setattr(adhkar, "DATA_PATH", tmp_path / "nope.json")
    adhkar.reset_cache()
    agent = AdhkarAgent()
    reply = await agent.handle(make_request("أذكار الصباح"))
    assert "fetch_adhkar.py" in reply.text
    adhkar.reset_cache()
