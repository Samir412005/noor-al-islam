"""اختبارات وكيل المناسبات — بلا شبكة.

نختبر `upcoming()` النقيّة بتثبيت التاريخ الهجري، والوكيل بردّ مزيّف للتاريخ الهجري.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.agents import occasions as occ
from app.models import AgentRequest, UserProfile
from app.services import prayer_api

FIXED_HIJRI = {
    "day": 20,
    "month_ar": "رمضان",
    "year": 1448,
    "month_number": 9,
    "text": "٢٠ رمضان ١٤٤٨هـ",
}

SAMPLE_OCCASIONS = [
    {"id": "ramadan", "title": "١ رمضان", "month": 9, "day": 1, "kind": "month_start", "hint": "x"},
    {"id": "laylat_al_qadr", "title": "٢٧ رمضان", "month": 9, "day": 27, "kind": "night", "hint": "x"},
    {"id": "eid_fitr", "title": "١ شوال", "month": 10, "day": 1, "kind": "eid", "hint": "x"},
    {"id": "eid_adha", "title": "١٠ ذو الحجة", "month": 12, "day": 10, "kind": "eid", "hint": "x"},
]


# ── الحساب النقي ────────────────────────────────────────────────────


def test_upcoming_sorted_by_remaining():
    items = occ.upcoming({"year": 1448, "month": 9, "day": 20}, 5, occasions=SAMPLE_OCCASIONS)
    assert [i["id"] for i in items] == ["laylat_al_qadr", "eid_fitr", "eid_adha", "ramadan"]
    assert items[0]["remaining_days"] == 7
    assert items[1]["remaining_days"] == 11
    assert items[2]["remaining_days"] == 79


def test_upcoming_returns_today_when_on_occasion():
    items = occ.upcoming({"year": 1448, "month": 9, "day": 27}, 5, occasions=SAMPLE_OCCASIONS)
    assert items[0]["id"] == "laylat_al_qadr"
    assert items[0]["remaining_days"] == 0
    assert occ.humanize_days(0) == "اليوم"


def test_upcoming_rolls_into_next_hijri_year():
    items = occ.upcoming({"year": 1448, "month": 12, "day": 15}, 5, occasions=SAMPLE_OCCASIONS)
    assert items[0]["id"] == "ramadan"
    assert items[0]["hijri_year"] == 1449
    assert items[0]["remaining_days"] > 0


def test_upcoming_limit_and_gregorian():
    items = occ.upcoming(
        {"year": 1448, "month": 9, "day": 20},
        2,
        occasions=SAMPLE_OCCASIONS,
        today=date(2026, 3, 9),
    )
    assert len(items) == 2
    assert items[0]["date_text"] == "١٦ مارس ٢٠٢٦"


def test_hijri_month_lengths_and_leap():
    assert occ.month_length(9, 1448) == 30
    assert occ.month_length(10, 1448) == 29
    assert occ.month_length(12, 1447) in (29, 30)
    assert isinstance(occ.is_leap_year(1448), bool)


def test_loaded_occasions_cover_required():
    ids = {item["id"] for item in occ.load_occasions()}
    assert {
        "ramadan",
        "laylat_al_qadr",
        "eid_fitr",
        "arafah",
        "eid_adha",
        "ashura",
        "isra_miraj",
        "mid_shaban",
        "hijri_new_year",
    } <= ids


# ── الوكيل ──────────────────────────────────────────────────────────


def _request(text: str) -> AgentRequest:
    return AgentRequest(text=text, chat_id=1, user=UserProfile(chat_id=1))


@pytest.mark.asyncio
async def test_agent_lists_upcoming(monkeypatch):
    async def fake_hijri(*args, **kwargs):
        return FIXED_HIJRI

    monkeypatch.setattr(prayer_api, "hijri", fake_hijri)
    agent = occ.OccasionsAgent()
    reply = await agent.handle(_request("المناسبات القادمة"))
    assert reply.agent == "occasions"
    assert "ليلة القدر" in reply.text
    assert "عيد الفطر" in reply.text
    assert "خالف الرؤية بيوم" in reply.text


@pytest.mark.asyncio
async def test_agent_nearest_action(monkeypatch):
    async def fake_hijri(*args, **kwargs):
        return FIXED_HIJRI

    monkeypatch.setattr(prayer_api, "hijri", fake_hijri)
    agent = occ.OccasionsAgent()
    request = AgentRequest(text="ما أقرب مناسبة؟", chat_id=1, user=UserProfile(chat_id=1), args="next")
    reply = await agent.handle(request)
    assert "أقرب مناسبة" in reply.text
    assert "ليلة القدر" in reply.text


@pytest.mark.asyncio
async def test_agent_error_is_polite(monkeypatch):
    async def broken(*args, **kwargs):
        raise prayer_api.PrayerAPIError("down")

    monkeypatch.setattr(prayer_api, "hijri", broken)
    agent = occ.OccasionsAgent()
    reply = await agent.handle(_request("متى رمضان"))
    assert "⚠️" in reply.text
    assert "Traceback" not in reply.text
