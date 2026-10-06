"""خدمة المواقيت — تغلّف AlAdhan API (بلا مفتاح).

كل النداءات تمرّ من العميل المشترك `http` (تخزين مؤقت + إعادة محاولة)،
وتُحوَّل كل الأخطاء إلى `PrayerAPIError` حتى لا تتسرّب استثناءات الشبكة
إلى الوكلاء (لا traceback للمستخدم).

ملاحظات مهمّة:
- AlAdhan يعيد الأوقات **بالتوقيت المحلي للإحداثيات** حسب حقل `meta.timezone`،
  لذلك لا نحتاج تحويلاً يدوياً.
- لا نستخدم `timingsByCity` (يعيد 302) — نستخدم الإحداثيات دائماً.
- للعثور على إحداثيات مدينة نستخدم geocoding من Open-Meteo، مع Nominatim كخطة بديلة.
"""
from __future__ import annotations

import re
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..text import to_arabic_digits
from .http import http

BASE = "https://api.aladhan.com/v1"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

TTL_TIMINGS = 600  # 10 دقائق
TTL_STATIC = 86400  # يوم: الهجري/القبلة/الإحداثيات

# طرق الحساب المدعومة في AlAdhan (معرّفات حقيقية) — 3 هو الافتراضي.
METHODS: dict[int, str] = {
    3: "رابطة العالم الإسلامي",
    2: "الجمعية الإسلامية لأمريكا الشمالية (ISNA)",
    4: "جامعة أم القرى، مكة",
    5: "الهيئة المصرية العامة للمساحة",
    1: "جامعة العلوم الإسلامية، كراتشي",
    0: "شيعي إثنا عشري",
    12: "الاتحاد الإسلامي الفرنسي",
    19: "الجزائر — وزارة الشؤون الدينية",
}
DEFAULT_METHOD = 3

# الترتيب المطلوب للعرض: الصلوات الخمس + الشروق.
PRAYER_ORDER: list[tuple[str, str]] = [
    ("Fajr", "الفجر"),
    ("Sunrise", "الشروق"),
    ("Dhuhr", "الظهر"),
    ("Asr", "العصر"),
    ("Maghrib", "المغرب"),
    ("Isha", "العشاء"),
]
# الصلوات المؤقّتة (تُستثنى الشمس من «الصلاة القادمة»).
PRAYERS: list[tuple[str, str]] = [(k, v) for k, v in PRAYER_ORDER if k != "Sunrise"]

AR_HIJRI_MONTHS = [
    "محرم",
    "صفر",
    "ربيع الأول",
    "ربيع الثاني",
    "جمادى الأولى",
    "جمادى الآخرة",
    "رجب",
    "شعبان",
    "رمضان",
    "شوال",
    "ذو القعدة",
    "ذو الحجة",
]

AR_GREGORIAN_MONTHS = [
    "يناير",
    "فبراير",
    "مارس",
    "أبريل",
    "مايو",
    "يونيو",
    "يوليو",
    "أغسطس",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
]

AR_WEEKDAYS = {
    "Monday": "الاثنين",
    "Tuesday": "الثلاثاء",
    "Wednesday": "الأربعاء",
    "Thursday": "الخميس",
    "Friday": "الجمعة",
    "Saturday": "السبت",
    "Sunday": "الأحد",
}

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


class PrayerAPIError(RuntimeError):
    """فشل في خدمة المواقيت (شبكة، استجابة غير متوقعة، أو مدخل غير صالح)."""


# ── أدوات داخلية ───────────────────────────────────────────────────


async def _get_json(url: str, params: dict | None = None, *, ttl: int = TTL_TIMINGS) -> Any:
    try:
        return await http.get_json(url, params=params, ttl=ttl)
    except Exception as exc:  # يشمل httpx وأخطاء JSON
        raise PrayerAPIError(f"تعذّر الوصول إلى الخدمة: {url}") from exc


def _coerce_date(value: date_cls | datetime | str | None) -> date_cls:
    if value is None:
        return date_cls.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    if isinstance(value, str):
        raw = value.strip()
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    raise PrayerAPIError("صيغة تاريخ غير صالحة")


def split_time(value: str) -> tuple[int, int]:
    """يستخرج (ساعة، دقيقة) من «05:12» أو «05:32 (CET)»."""
    match = _TIME_RE.search(str(value or ""))
    if not match:
        raise PrayerAPIError("قيمة وقت غير صالحة")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise PrayerAPIError("قيمة وقت خارج النطاق")
    return hour, minute


def clean_time(value: str) -> str:
    """يعيد «HH:MM» نظيفاً بلا لاحقة المنطقة الزمنية."""
    hour, minute = split_time(value)
    return f"{hour:02d}:{minute:02d}"


def format_time_ar(hhmm: str) -> str:
    """«05:12» ← «٥:١٢ ص»، «13:05» ← «١:٠٥ م»."""
    hour, minute = split_time(hhmm)
    suffix = "ص" if hour < 12 else "م"
    hour12 = hour % 12 or 12
    return f"{to_arabic_digits(hour12)}:{to_arabic_digits(f'{minute:02d}')} {suffix}"


def _hijri_text(obj: dict) -> str:
    day = int(obj.get("day") or 0)
    year = int(obj.get("year") or 0)
    month_number = int((obj.get("month") or {}).get("number") or 0)
    month_ar = AR_HIJRI_MONTHS[month_number - 1] if 1 <= month_number <= 12 else str(
        (obj.get("month") or {}).get("ar") or ""
    )
    return f"{to_arabic_digits(day)} {month_ar} {to_arabic_digits(year)}هـ"


def gregorian_text(value: date_cls | datetime | None = None) -> str:
    """تاريخ ميلادي بالعربية: «الثلاثاء ٦ أكتوبر ٢٠٢٦»."""
    day = value.date() if isinstance(value, datetime) else (value or date_cls.today())
    weekday = AR_WEEKDAYS.get(day.strftime("%A"), "")
    month_ar = AR_GREGORIAN_MONTHS[day.month - 1]
    core = f"{to_arabic_digits(day.day)} {month_ar} {to_arabic_digits(day.year)}"
    return f"{weekday} {core}".strip()


def _gregorian_from_payload(obj: dict) -> str:
    try:
        day = int(obj.get("day") or 0)
        month_number = int((obj.get("month") or {}).get("number") or 0)
        year = int(obj.get("year") or 0)
        weekday = AR_WEEKDAYS.get(((obj.get("weekday") or {}).get("en") or ""), "")
        month_ar = AR_GREGORIAN_MONTHS[month_number - 1]
        core = f"{to_arabic_digits(day)} {month_ar} {to_arabic_digits(year)}"
        return f"{weekday} {core}".strip()
    except (ValueError, IndexError, TypeError):
        return ""


def _method_name(meta: dict, fallback: int) -> str:
    method_obj = meta.get("method") or {}
    method_id = method_obj.get("id")
    if method_id is None:
        method_id = fallback
    try:
        method_id = int(method_id)
    except (TypeError, ValueError):
        method_id = fallback
    return METHODS.get(method_id) or str(method_obj.get("name") or METHODS[DEFAULT_METHOD])


# ── الواجهة العامة ─────────────────────────────────────────────────


async def timings(
    lat: float,
    lon: float,
    *,
    method: int = DEFAULT_METHOD,
    date: date_cls | datetime | str | None = None,
    tz: str | None = None,
) -> dict:
    """مواقيت الصلاة ليوم واحد بتوقيت الإحداثيات، منسّقة بالعربية."""
    if lat is None or lon is None:
        raise PrayerAPIError("إحداثيات غير صالحة")
    day = _coerce_date(date)
    payload = await _get_json(
        f"{BASE}/timings/{day.strftime('%d-%m-%Y')}",
        params={
            "latitude": lat,
            "longitude": lon,
            "method": int(method),
            "school": 0,
        },
        ttl=TTL_TIMINGS,
    )
    data = (payload or {}).get("data") or {}
    raw = data.get("timings") or {}
    if not raw:
        raise PrayerAPIError("استجابة مواقيت فارغة")

    timings_map: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []
    for key, label in PRAYER_ORDER:
        if not raw.get(key):
            raise PrayerAPIError(f"وقت مفقود: {key}")
        value = clean_time(raw[key])
        timings_map[key] = value
        ordered.append((label, value))

    date_obj = data.get("date") or {}
    meta = data.get("meta") or {}
    timezone = meta.get("timezone") or tz or "UTC"
    return {
        "date_text": _gregorian_from_payload(date_obj.get("gregorian") or {}),
        "hijri_text": _hijri_text(date_obj.get("hijri") or {}),
        "timings": timings_map,
        "ordered": ordered,
        "meta": {
            "timezone": timezone,
            "method_name": _method_name(meta, method),
            "method_id": (meta.get("method") or {}).get("id", method),
            "latitude": lat,
            "longitude": lon,
        },
    }


def _plural_ar(n: int, singular: str, dual: str, plural: str, many: str) -> str:
    if n == 1:
        return singular
    if n == 2:
        return dual
    if 3 <= n <= 10:
        return f"{to_arabic_digits(n)} {plural}"
    return f"{to_arabic_digits(n)} {many}"


def humanize_remaining(seconds: int) -> str:
    """«بقي ساعتان و١٥ دقيقة» من عدد الثواني."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "بقي أقل من دقيقة"
    total_minutes = seconds // 60
    hours, minutes = divmod(total_minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(_plural_ar(hours, "ساعة", "ساعتان", "ساعات", "ساعة"))
    if minutes or not hours:
        parts.append(_plural_ar(minutes, "دقيقة", "دقيقتان", "دقائق", "دقيقة"))
    return "بقي " + " و".join(parts)


async def next_prayer(
    lat: float,
    lon: float,
    method: int = DEFAULT_METHOD,
    *,
    now: datetime | None = None,
) -> dict:
    """الصلاة القادمة بالنسبة لوقت الجهاز في منطقة المستخدم الزمنية."""
    data = await timings(lat, lon, method=method)
    tz_name = data["meta"]["timezone"] or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover - منطقة نادرة
        tzinfo = ZoneInfo("UTC")

    current = now or datetime.now(tzinfo)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tzinfo)
    else:
        current = current.astimezone(tzinfo)

    candidates: list[tuple[datetime, str, str]] = []
    for key, label in PRAYERS:
        hour, minute = split_time(data["timings"][key])
        at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= current:
            at += timedelta(days=1)
        candidates.append((at, label, key))

    at, label, key = min(candidates, key=lambda item: item[0])
    remaining = int((at - current).total_seconds())
    return {
        "name": label,
        "name_key": key,
        "at": at,
        "time_24": data["timings"][key],
        "time_text": format_time_ar(data["timings"][key]),
        "remaining_seconds": remaining,
        "remaining_text": humanize_remaining(remaining),
        "date_text": data["date_text"],
        "hijri_text": data["hijri_text"],
        "timezone": tz_name,
    }


async def qibla(lat: float, lon: float) -> float:
    """اتجاه القبلة بالدرجات عن الشمال الحقيقي."""
    if lat is None or lon is None:
        raise PrayerAPIError("إحداثيات غير صالحة")
    payload = await _get_json(f"{BASE}/qibla/{lat}/{lon}", ttl=TTL_STATIC)
    direction = ((payload or {}).get("data") or {}).get("direction")
    if direction is None:
        raise PrayerAPIError("تعذّر حساب القبلة")
    return float(direction)


async def hijri(date: date_cls | datetime | str | None = None) -> dict:
    """التاريخ الهجري المقابل لتاريخ ميلادي (اليوم افتراضياً)."""
    day = _coerce_date(date)
    payload = await _get_json(f"{BASE}/gToH/{day.strftime('%d-%m-%Y')}", ttl=TTL_STATIC)
    obj = ((payload or {}).get("data") or {}).get("hijri") or {}
    if not obj:
        raise PrayerAPIError("استجابة هجري فارغة")
    month_number = int((obj.get("month") or {}).get("number") or 0)
    return {
        "day": int(obj.get("day") or 0),
        "month_ar": AR_HIJRI_MONTHS[month_number - 1]
        if 1 <= month_number <= 12
        else str((obj.get("month") or {}).get("ar") or ""),
        "year": int(obj.get("year") or 0),
        "month_number": month_number,
        "text": _hijri_text(obj),
    }


async def monthly(
    lat: float,
    lon: float,
    method: int = DEFAULT_METHOD,
    year: int | None = None,
    month: int | None = None,
) -> list[dict]:
    """تقويم المواقيت لشهر ميلادي كامل."""
    today = date_cls.today()
    year = int(year or today.year)
    month = int(month or today.month)
    if not (1 <= month <= 12):
        raise PrayerAPIError("شهر غير صالح")
    payload = await _get_json(
        f"{BASE}/calendar/{year}/{month}",
        params={"latitude": lat, "longitude": lon, "method": int(method)},
        ttl=TTL_TIMINGS,
    )
    rows = (payload or {}).get("data") or []
    if not rows:
        raise PrayerAPIError("استجابة تقويم فارغة")

    out: list[dict] = []
    for row in rows:
        raw = row.get("timings") or {}
        date_obj = row.get("date") or {}
        timings_map: dict[str, str] = {}
        ordered: list[tuple[str, str]] = []
        for key, label in PRAYER_ORDER:
            if not raw.get(key):
                continue
            value = clean_time(raw[key])
            timings_map[key] = value
            ordered.append((label, value))
        if not timings_map:
            continue
        gregorian = date_obj.get("gregorian") or {}
        out.append(
            {
                "day": int(gregorian.get("day") or 0),
                "date_text": _gregorian_from_payload(gregorian),
                "short_date": f"{to_arabic_digits(int(gregorian.get('day') or 0))} "
                f"{AR_GREGORIAN_MONTHS[int((gregorian.get('month') or {}).get('number') or 1) - 1]}",
                "hijri_text": _hijri_text(date_obj.get("hijri") or {}),
                "timings": timings_map,
                "ordered": ordered,
            }
        )
    return out


async def find_city_coords(city: str) -> dict | None:
    """إحداثيات مدينة عبر Open-Meteo، مع Nominatim كخطة بديلة."""
    name = (city or "").strip()
    if not name:
        return None

    try:
        payload = await _get_json(
            GEOCODE_URL,
            params={"name": name, "count": 1, "language": "ar", "format": "json"},
            ttl=TTL_STATIC,
        )
        results = (payload or {}).get("results") or []
        if results:
            item = results[0]
            return {
                "name": item.get("name") or name,
                "latitude": float(item["latitude"]),
                "longitude": float(item["longitude"]),
                "country": item.get("country") or "",
                "timezone": item.get("timezone") or "",
            }
    except (PrayerAPIError, KeyError, ValueError, TypeError):
        pass

    # خطة بديلة: Nominatim (العميل المشترك يضع User-Agent مناسباً).
    try:
        payload = await _get_json(
            NOMINATIM_URL,
            params={"q": name, "format": "json", "limit": 1, "accept-language": "ar"},
            ttl=TTL_STATIC,
        )
        if payload:
            item = payload[0]
            display = str(item.get("display_name") or "").split(",")[0].strip()
            return {
                "name": display or name,
                "latitude": float(item["lat"]),
                "longitude": float(item["lon"]),
                "country": "",
                "timezone": "",
            }
    except (PrayerAPIError, KeyError, ValueError, TypeError, IndexError):
        pass

    return None
