"""اختبارات وكيل القرآن وخدمته — بلا أي وصول للشبكة.

نعتمد على ملف البيانات المحلي الذي ينتجه scripts/fetch_quran.py، ونحجب
عميل HTTP بحقن دالة وهمية عند اختبار مسارات التفسير/الفشل.
"""
from __future__ import annotations

import unicodedata

import pytest

from app.agents.quran import QuranAgent
from app.models import AgentRequest, UserProfile
from app.services import quran_api

pytestmark = pytest.mark.asyncio

if not quran_api.DATA_FILE.exists():  # pragma: no cover - يلزم تشغيل السكربت أولاً
    pytest.skip("ملف بيانات القرآن غير موجود — شغّل scripts/fetch_quran.py", allow_module_level=True)


def _nfc(text: str) -> str:
    """توحيد ترتيب علامات التشكيل لتفادي فروق الترميز في المقارنة."""
    return unicodedata.normalize("NFC", text)


def _request(text: str) -> AgentRequest:
    user = UserProfile(chat_id=1)
    return AgentRequest(text=text, chat_id=1, user=user)


def _block_network(monkeypatch, *, result=None, error: Exception | None = None) -> None:
    async def fake_get_json(url, params=None, *, ttl=900):
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(quran_api.http, "get_json", fake_get_json)


# ── find_surah ──────────────────────────────────────────────────────
async def test_find_surah_by_number_and_arabic_and_english():
    assert quran_api.find_surah("2")["number"] == 2
    assert quran_api.find_surah("٢")["number"] == 2
    assert quran_api.find_surah("114")["name"] == "الناس"
    assert quran_api.find_surah("الفاتحة")["number"] == 1
    assert quran_api.find_surah("فاتحة")["number"] == 1        # بلا أل التعريف
    assert quran_api.find_surah("البقرة")["number"] == 2
    assert quran_api.find_surah("البقره")["number"] == 2        # بلا تشكيل/تاء مربوطة
    assert quran_api.find_surah("سورة الملك")["number"] == 67
    assert quran_api.find_surah("Al-Fatiha")["number"] == 1     # إنجليزي case-insensitive
    assert quran_api.find_surah("al-baqara")["number"] == 2


async def test_find_surah_failure_returns_none():
    assert quran_api.find_surah("زطظ") is None
    assert quran_api.find_surah("999") is None
    assert quran_api.find_surah("") is None


# ── get_ayah / global number ────────────────────────────────────────
async def test_get_ayah_and_out_of_range():
    ayah = quran_api.get_ayah(2, 255)
    assert ayah["surah"] == 2
    assert ayah["surah_name"] == "البقرة"
    assert ayah["ayah"] == 255
    assert ayah["global_number"] == 262
    assert _nfc(ayah["text"]).startswith(_nfc("ٱللَّهُ"))  # توحيد النص

    with pytest.raises(ValueError):
        quran_api.get_ayah(2, 300)
    with pytest.raises(ValueError):
        quran_api.get_ayah(115, 1)
    with pytest.raises(ValueError):
        quran_api.get_ayah(0, 1)


async def test_global_ayah_number_known_values():
    assert quran_api.global_ayah_number(1, 1) == 1
    assert quran_api.global_ayah_number(2, 255) == 262
    assert quran_api.global_ayah_number(114, 6) == 6236


async def test_ayah_audio_url():
    assert quran_api.ayah_audio_url(2, 255) == (
        "https://cdn.islamic.network/quran/audio/128/ar.alafasy/262.mp3"
    )
    assert set(quran_api.RECITERS) >= {"ar.alafasy", "ar.husary"}


# ── surah_list ──────────────────────────────────────────────────────
async def test_surah_list_shape():
    surahs = quran_api.surah_list()
    assert len(surahs) == 114
    assert surahs[0] == {
        "number": 1,
        "name": "الفاتحة",
        "englishName": surahs[0]["englishName"],
        "ayahs_count": 7,
    }


# ── search ──────────────────────────────────────────────────────────
async def test_search_ayahs_yields_results():
    results = quran_api.search_ayahs("الصبر", limit=6)
    assert results, "بحث «الصبر» يجب أن يعيد نتائج"
    for item in results:
        assert set(item) == {"surah", "surah_name", "ayah", "text", "global_number"}
        assert item["text"]


async def test_search_ayahs_short_query_is_empty():
    assert quran_api.search_ayahs("ص") == []
    assert quran_api.search_ayahs("") == []


# ── verse of the day ────────────────────────────────────────────────
async def test_verse_of_the_day_stable_within_day():
    first = quran_api.verse_of_the_day()
    second = quran_api.verse_of_the_day()
    assert first == second
    assert (first["surah"], first["ayah"]) in quran_api.VERSE_OF_THE_DAY


# ── التفسير مع حجب الشبكة ────────────────────────────────────────────
async def test_tafsir_uses_injected_client(monkeypatch):
    _block_network(monkeypatch, result={"data": {"text": "نصّ التفسير التجريبي."}})
    text = await quran_api.tafsir(2, 255)
    assert text == "نصّ التفسير التجريبي."


async def test_ayah_details_survives_tafsir_failure(monkeypatch):
    _block_network(monkeypatch, error=RuntimeError("offline"))
    details = await quran_api.ayah_details(2, 255)
    assert details["global_number"] == 262
    assert _nfc(details["text"]).startswith(_nfc("ٱللَّهُ"))
    assert details["tafsir"] == ""  # فشل التفسير لا يُفشل الآية


# ── الوكيل ──────────────────────────────────────────────────────────
async def test_agent_handles_reference_without_network(monkeypatch):
    _block_network(monkeypatch, error=RuntimeError("offline"))
    agent = QuranAgent()
    reply = await agent.handle(_request("2:255"))
    assert reply.agent == "quran"
    assert _nfc("ٱللَّهُ") in _nfc(reply.text)
    assert any("tafsir:2:255" in b.data for row in reply.buttons for b in row)


async def test_agent_handles_verse_of_the_day_without_network(monkeypatch):
    _block_network(monkeypatch, error=RuntimeError("offline"))
    agent = QuranAgent()
    reply = await agent.handle(_request("آية اليوم"))
    assert reply.agent == "quran"
    assert "آية اليوم" in reply.text
    assert reply.sources


async def test_agent_tafsir_failure_is_graceful(monkeypatch):
    _block_network(monkeypatch, error=RuntimeError("offline"))
    agent = QuranAgent()
    reply = await agent.handle(_request("تفسير 2:255"))  # يجب ألّا يرفع استثناء
    assert isinstance(reply.text, str) and reply.text
    assert reply.agent == "quran"


async def test_agent_tafsir_success_with_fake_network(monkeypatch):
    _block_network(monkeypatch, result={"data": {"text": "تفسير ميسّر تجريبي."}})
    agent = QuranAgent()
    reply = await agent.handle(_request("تفسير 2:255"))
    assert "تفسير ميسّر تجريبي." in reply.text
    assert any("التفسير" in s for s in reply.sources)


async def test_agent_surah_and_search(monkeypatch):
    _block_network(monkeypatch, error=RuntimeError("offline"))
    agent = QuranAgent()
    surah_reply = await agent.handle(_request("سورة الملك"))
    assert "الملك" in surah_reply.text
    assert any("surah:67" in b.data for row in surah_reply.buttons for b in row)

    search_reply = await agent.handle(_request("ابحث عن الصبر"))
    assert search_reply.text
