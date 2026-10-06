"""اختبارات وكيل المواقيت وخدمة AlAdhan — بلا شبكة.

الردود المزيّفة أدناه مأخوذة حرفياً (بنيوياً) من استجابات AlAdhan الحقيقية
المُلتقطة بـ curl، مع monkeypatch على `prayer_api.http.get_json`.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.prayer import PrayerAgent, compass_ar
from app.models import AgentRequest, UserProfile
from app.services import prayer_api

# ── ردود مزيّفة ببُنية AlAdhan الحقيقية ────────────────────────────

TIMINGS_PAYLOAD = {
    "code": 200,
    "status": "OK",
    "data": {
        "timings": {
            "Fajr": "05:37",
            "Sunrise": "07:01",
            "Dhuhr": "12:51",
            "Asr": "16:09",
            "Sunset": "18:39",
            "Maghrib": "18:39",
            "Isha": "19:59",
            "Imsak": "05:27",
            "Midnight": "00:50",
        },
        "date": {
            "readable": "06 Oct 2026",
            "timestamp": "1791266400",
            "hijri": {
                "date": "25-04-1448",
                "day": "25",
                "weekday": {"en": "Al Thalaata", "ar": "الثلاثاء"},
                "month": {"number": 4, "en": "Rabīʿ al-thānī", "ar": "رَبيع الثاني", "days": 30},
                "year": "1448",
                "designation": {"abbreviated": "AH", "expanded": "Anno Hegirae"},
            },
            "gregorian": {
                "date": "06-10-2026",
                "day": "06",
                "weekday": {"en": "Tuesday"},
                "month": {"number": 10, "en": "October", "ar": ""},
                "year": "2026",
            },
        },
        "meta": {
            "latitude": 35.69906,
            "longitude": -0.63588,
            "timezone": "Africa/Algiers",
            "method": {
                "id": 3,
                "name": "Muslim World League",
                "params": {"Fajr": 18, "Isha": 17},
            },
            "school": "STANDARD",
        },
    },
}

QUIBLA_PAYLOAD = {
    "code": 200,
    "status": "OK",
    "data": {"latitude": 35.69906, "longitude": -0.63588, "direction": 100.93368199398056},
}

GTOH_PAYLOAD = {
    "code": 200,
    "status": "OK",
    "data": {
        "hijri": {
            "date": "25-04-1448",
            "day": "25",
            "weekday": {"en": "Al Thalaata", "ar": "الثلاثاء"},
            "month": {"number": 4, "en": "Rabīʿ al-thānī", "ar": "رَبيع الثاني", "days": 30},
            "year": "1448",
            "designation": {"abbreviated": "AH"},
        },
        "gregorian": {"date": "06-10-2026", "day": "06", "year": "2026"},
    },
}

GEOCODE_PAYLOAD = {
    "results": [
        {
            "id": 2485926,
            "name": "وهران",
            "latitude": 35.69906,
            "longitude": -0.63588,
            "elevation": 118.0,
            "timezone": "Africa/Algiers",
            "country": "الجزائر",
            "admin1": "ولاية وهران",
        }
    ],
    "generationtime_ms": 0.91,
}

CALENDAR_PAYLOAD = {
    "code": 200,
    "status": "OK",
    "data": [
        {
            "timings": {
                "Fajr": "05:32 (CET)",
                "Sunrise": "06:57 (CET)",
                "Dhuhr": "12:52 (CET)",
                "Asr": "16:14 (CET)",
                "Maghrib": "18:46 (CET)",
                "Isha": "20:06 (CET)",
            },
            "date": {
                "gregorian": {
                    "date": "01-10-2026",
                    "day": "01",
                    "month": {"number": 10, "en": "October"},
                    "year": "2026",
                },
                "hijri": {"day": "20", "month": {"number": 4}, "year": "1448"},
            },
        },
        {
            "timings": {
                "Fajr": "05:33 (CET)",
                "Sunrise": "06:58 (CET)",
                "Dhuhr": "12:52 (CET)",
                "Asr": "16:13 (CET)",
                "Maghrib": "18:45 (CET)",
                "Isha": "20:05 (CET)",
            },
            "date": {
                "gregorian": {
                    "date": "02-10-2026",
                    "day": "02",
                    "month": {"number": 10, "en": "October"},
                    "year": "2026",
                },
                "hijri": {"day": "21", "month": {"number": 4}, "year": "1448"},
            },
        },
    ],
}


async def fake_get_json(url: str, params: dict | None = None, ttl: int = 900):
    if "geocoding-api" in url:
        return GEOCODE_PAYLOAD
    if "/qibla/" in url:
        return QUIBLA_PAYLOAD
    if "/gToH/" in url:
        return GTOH_PAYLOAD
    if "/calendar/" in url:
        return CALENDAR_PAYLOAD
    if "/timings/" in url:
        return TIMINGS_PAYLOAD
    raise RuntimeError(f"unexpected url: {url}")


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(prayer_api.http, "get_json", fake_get_json)
    return None


# ── format_time_ar ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("05:12", "٥:١٢ ص"),
        ("13:05", "١:٠٥ م"),
        ("00:30", "١٢:٣٠ ص"),
        ("12:00", "١٢:٠٠ م"),
        ("05:32 (CET)", "٥:٣٢ ص"),
    ],
)
def test_format_time_ar(raw, expected):
    assert prayer_api.format_time_ar(raw) == expected


def test_split_time_rejects_garbage():
    with pytest.raises(prayer_api.PrayerAPIError):
        prayer_api.split_time("ليس وقتاً")


# ── timings / ordered ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timings_shapes(offline):
    data = await prayer_api.timings(35.69906, -0.63588, method=3)
    labels = [name for name, _ in data["ordered"]]
    assert labels == ["الفجر", "الشروق", "الظهر", "العصر", "المغرب", "العشاء"]
    assert data["timings"]["Fajr"] == "05:37"
    assert data["timings"]["Isha"] == "19:59"
    assert data["ordered"][0] == ("الفجر", "05:37")
    assert "أكتوبر" in data["date_text"]
    assert "ربيع الثاني ١٤٤٨هـ" in data["hijri_text"]
    assert data["meta"]["timezone"] == "Africa/Algiers"
    assert data["meta"]["method_name"] == "رابطة العالم الإسلامي"


# ── qibla ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_qibla(offline):
    direction = await prayer_api.qibla(35.69906, -0.63588)
    assert direction == pytest.approx(100.93368199398056)


def test_compass_ar():
    assert compass_ar(100.9) == "شرقاً"
    assert compass_ar(135) == "جنوباً-شرقاً"
    assert compass_ar(0) == "شمالاً"


# ── hijri ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hijri_text(offline):
    data = await prayer_api.hijri("06-10-2026")
    assert data["day"] == 25
    assert data["month_number"] == 4
    assert data["year"] == 1448
    assert data["text"] == "٢٥ ربيع الثاني ١٤٤٨هـ"


# ── monthly ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_monthly(offline):
    days = await prayer_api.monthly(35.69906, -0.63588, year=2026, month=10)
    assert len(days) == 2
    assert days[0]["day"] == 1
    assert days[0]["timings"]["Fajr"] == "05:32"
    assert days[0]["short_date"] == "١ أكتوبر"


# ── find_city_coords ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_city_coords(offline):
    coords = await prayer_api.find_city_coords("وهران")
    assert coords["name"] == "وهران"
    assert coords["latitude"] == pytest.approx(35.69906)
    assert coords["timezone"] == "Africa/Algiers"
    assert coords["country"] == "الجزائر"


@pytest.mark.asyncio
async def test_find_city_coords_empty(offline):
    assert await prayer_api.find_city_coords("   ") is None


# ── next_prayer ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_next_prayer_fixed_morning(offline):
    now = datetime(2026, 10, 6, 5, 0, tzinfo=ZoneInfo("Africa/Algiers"))
    result = await prayer_api.next_prayer(35.69906, -0.63588, now=now)
    assert result["name"] == "الفجر"
    assert result["remaining_seconds"] == 37 * 60
    assert result["remaining_text"] == "بقي ٣٧ دقيقة"


@pytest.mark.asyncio
async def test_next_prayer_after_isha_rolls_to_fajr(offline):
    now = datetime(2026, 10, 6, 23, 0, tzinfo=ZoneInfo("Africa/Algiers"))
    result = await prayer_api.next_prayer(35.69906, -0.63588, now=now)
    assert result["name"] == "الفجر"
    assert result["remaining_seconds"] == 6 * 3600 + 37 * 60


@pytest.mark.asyncio
async def test_next_prayer_humanize(offline):
    now = datetime(2026, 10, 6, 10, 36, tzinfo=ZoneInfo("Africa/Algiers"))
    result = await prayer_api.next_prayer(35.69906, -0.63588, now=now)
    assert result["name"] == "الظهر"
    assert result["remaining_text"].startswith("بقي ")
    assert "دقيقة" in result["remaining_text"]


# ── الوكيل: لا يرفع استثناء ─────────────────────────────────────────


def _request(text: str, **prefs) -> AgentRequest:
    user = UserProfile(chat_id=1, **prefs)
    return AgentRequest(text=text, chat_id=1, user=user)


@pytest.mark.asyncio
async def test_agent_timings_with_city(offline, monkeypatch):
    async def noop_update(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.prayer.db.update_user", noop_update)
    agent = PrayerAgent()
    reply = await agent.handle(_request("مواقيت الصلاة في وهران"))
    assert reply.agent == "prayer"
    assert "وهران" in reply.text
    assert "الفجر" in reply.text
    assert reply.buttons and reply.buttons[0][0].data == "ag:prayer:timings"
    assert prayer_api.METHODS[3] in reply.text


@pytest.mark.asyncio
async def test_agent_timings_default_profile(offline):
    agent = PrayerAgent()
    reply = await agent.handle(_request("مواقيت الصلاة"))
    assert "الجزائر" in reply.text
    assert "٥:٣٧ ص" in reply.text


@pytest.mark.asyncio
async def test_agent_qibla(offline):
    agent = PrayerAgent()
    reply = await agent.handle(_request("القبلة"))
    assert "القبلة" in reply.text
    assert "١٠٠" in reply.text  # الدرجات بالأرقام العربية


@pytest.mark.asyncio
async def test_agent_hijri(offline):
    agent = PrayerAgent()
    reply = await agent.handle(_request("التاريخ الهجري"))
    assert "١٤٤٨" in reply.text


@pytest.mark.asyncio
async def test_agent_remaining_for_prayer(offline):
    agent = PrayerAgent()
    reply = await agent.handle(_request("كم بقي للفجر"))
    assert "الفجر" in reply.text
    assert "بقي" in reply.text


@pytest.mark.asyncio
async def test_agent_monthly(offline):
    agent = PrayerAgent()
    reply = await agent.handle(_request("شهر كامل"))
    assert "أكتوبر" in reply.text


@pytest.mark.asyncio
async def test_agent_network_error_is_polite(monkeypatch):
    async def broken(url, params=None, ttl=900):
        raise RuntimeError("network down")

    monkeypatch.setattr(prayer_api.http, "get_json", broken)
    agent = PrayerAgent()
    reply = await agent.handle(_request("مواقيت الصلاة"))
    assert "⚠️" in reply.text
    assert "Traceback" not in reply.text
    assert reply.agent == "prayer"


@pytest.mark.asyncio
async def test_agent_unknown_city(offline, monkeypatch):
    async def empty_city(city):
        return None

    monkeypatch.setattr(prayer_api, "find_city_coords", empty_city)
    agent = PrayerAgent()
    reply = await agent.handle(_request("مواقيت الصلاة في مدينتهم"))
    assert "لم أجد المدينة" in reply.text
