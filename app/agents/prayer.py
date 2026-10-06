"""وكيل المواقيت: مواقيت الصلاة، القبلة، التاريخ الهجري، والمتبقّي للصلاة.

يعتمد كلياً على `prayer_api` (AlAdhan) وعلى الملف الشخصي للمستخدم،
ويُعيد `AgentReply` مستقلّاً عن تيليجرام مع أزرار Inline.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..db import db
from ..models import AgentReply, AgentRequest, Button
from ..services import prayer_api
from ..services.prayer_api import PrayerAPIError
from ..text import clean, esc, normalize_arabic, to_arabic_digits
from .base import BaseAgent

SOURCE_TIMINGS = "AlAdhan API — حساب المواقيت"
SOURCE_HIJRI = "تقويم أم القرى (تحويل هجري)"

# كلمات تُحذف عند استخراج اسم المدينة من الجملة (بعد التطبيع).
_CITY_STOPWORDS = {
    "مواقيت",
    "مواقيت",
    "الصلاه",
    "صلاه",
    "صلاة",
    "اليوم",
    "الان",
    "اوقات",
    "وقت",
    "وقتي",
    "في",
    "مدينه",
    "مدينة",
    "بمدينه",
    "القبله",
    "قبله",
    "الهجري",
    "التاريخ",
    "الشهر",
    "شهر",
    "كامل",
    "كم",
    "باقي",
    "بقي",
    "المتبقي",
    "متبقي",
    "ل",
    "من",
    "على",
}

# اسم الصلاة ← مفتاح AlAdhan (صيغ بلا «ال» لتصمد أمام «للفجر»).
_PRAYER_WORDS: dict[str, str] = {
    "فجر": "Fajr",
    "شروق": "Sunrise",
    "ظهر": "Dhuhr",
    "عصر": "Asr",
    "مغرب": "Maghrib",
    "عشاء": "Isha",
}

_COMPASS = [
    "شمالاً",
    "شمالاً-شرقاً",
    "شرقاً",
    "جنوباً-شرقاً",
    "جنوباً",
    "جنوباً-غرباً",
    "غرباً",
    "شمالاً-غرباً",
]


def compass_ar(degrees: float) -> str:
    """وصف الاتجاه بالعربية من الدرجات."""
    index = int((degrees % 360) / 45.0 + 0.5) % 8
    return _COMPASS[index]


class PrayerAgent(BaseAgent):
    name = "prayer"
    title = "وكيل المواقيت"
    description = "مواقيت الصلاة، القبلة، والتاريخ الهجري."
    priority = 90
    keywords = [
        "مواقيت",
        "الصلاة",
        "صلاة",
        "الفجر",
        "الظهر",
        "العصر",
        "المغرب",
        "العشاء",
        "الشروق",
        "الأذان",
        "الاذان",
        "القبلة",
        "القبله",
        "الهجري",
        "التاريخ الهجري",
        "أوقات الصلاة",
        "المتبقي",
        "كم باقي", "كم بقي", "كم تبقّى", "كم تبقي",
        "مدينة",
        "وقتي",
    ]
    examples = [
        "مواقيت الصلاة",
        "مواقيت الصلاة في وهران",
        "القبلة",
        "التاريخ الهجري",
        "كم بقي للفجر",
    ]

    # ── نقطة الدخول ────────────────────────────────────────────────
    async def handle(self, request: AgentRequest) -> AgentReply:
        text = normalize_arabic(request.text or "")
        action = (request.args or "").strip().lower()

        try:
            if action == "qibla" or "قبله" in text:
                return await self._qibla(request)
            if action == "hijri" or "هجري" in text:
                return await self._hijri(request)
            if action == "monthly" or "شهر كامل" in text or "تقويم" in text or "الشهر" in text:
                return await self._monthly(request)
            if (
                action in {"next", "remaining"}
                or "كم باقي" in text
                or "كم بقي" in text
                or "متبقي" in text
                or "باقي" in text
                or "بقي" in text
            ):
                return await self._remaining(request, text)
            return await self._timings(request, self._extract_city(request.text))
        except PrayerAPIError:
            return self._error("⚠️ تعذّر الوصول إلى خدمة المواقيت الآن.")
        except Exception:  # دفاع نهائي: لا traceback للمستخدم
            return self._error("⚠️ حدث خطأ غير متوقّع أثناء جلب المواقيت.")

    # ── سلوك 1: المواقيت ───────────────────────────────────────────
    async def _timings(self, request: AgentRequest, city: str | None) -> AgentReply:
        coords = await self._resolve(request, city)
        if coords is None:
            return self._city_not_found(city)

        lat, lon, tz, label, method = coords
        data = await prayer_api.timings(lat, lon, method=method, tz=tz)

        width = max(len(name) for name, _ in data["ordered"]) + 3
        rows = "\n".join(
            f"{name:<{width}}{prayer_api.format_time_ar(value)}"
            for name, value in data["ordered"]
        )

        body = (
            f"🕌 <b>مواقيت الصلاة — {esc(label)}</b>\n"
            f"<i>{esc(data['date_text'])} — {esc(data['hijri_text'])}</i>\n\n"
            f"<code>{esc(rows)}</code>\n\n"
            f"<i>الطريقة: {esc(data['meta']['method_name'])} • "
            f"المنطقة الزمنية: {esc(data['meta']['timezone'])}</i>"
        )
        reply = AgentReply(
            text=clean(body),
            agent=self.name,
            buttons=[
                [
                    Button("تحديث 🔄", "ag:prayer:timings"),
                    Button("شهر كامل 📅", "ag:prayer:monthly"),
                ],
                [Button("القبلة 🧭", "ag:prayer:qibla")],
            ],
        )
        reply.with_source(SOURCE_TIMINGS).with_source(SOURCE_HIJRI)
        return reply

    # ── سلوك 2: القبلة ─────────────────────────────────────────────
    async def _qibla(self, request: AgentRequest) -> AgentReply:
        coords = await self._resolve(request, self._extract_city(request.text))
        if coords is None:
            return self._city_not_found(request.text)
        lat, lon, _tz, label, _method = coords
        degrees = await prayer_api.qibla(lat, lon)
        direction = compass_ar(degrees)
        body = (
            f"🧭 <b>اتجاه القبلة</b>\n"
            f"من {esc(label)}: <code>{to_arabic_digits(f'{degrees:.1f}')}°</code> عن الشمال\n"
            f"اتجه <b>{esc(direction)}</b> نحو الكعبة الشريفة."
        )
        reply = AgentReply(
            text=body,
            agent=self.name,
            buttons=[
                [
                    Button("المواقيت 🕌", "ag:prayer:timings"),
                    Button("تحديث 🔄", "ag:prayer:qibla"),
                ]
            ],
        )
        reply.with_source(SOURCE_TIMINGS)
        return reply

    # ── سلوك 3: التاريخ الهجري ─────────────────────────────────────
    async def _hijri(self, request: AgentRequest) -> AgentReply:
        data = await prayer_api.hijri()
        body = (
            f"📅 <b>التاريخ الهجري</b>\n"
            f"<code>{esc(data['text'])}</code>\n"
            f"<i>الموافق {esc(prayer_api.gregorian_text())}</i>"
        )
        reply = AgentReply(
            text=body,
            agent=self.name,
            buttons=[
                [
                    Button("المواقيت 🕌", "ag:prayer:timings"),
                    Button("المناسبات 📿", "ag:occasions:list"),
                ]
            ],
        )
        reply.with_source(SOURCE_HIJRI)
        return reply

    # ── سلوك 4: المتبقّي لصلاة ─────────────────────────────────────
    async def _remaining(self, request: AgentRequest, text: str) -> AgentReply:
        coords = await self._resolve(request, self._extract_city(request.text))
        if coords is None:
            return self._city_not_found(request.text)
        lat, lon, tz, label, method = coords

        key = next((v for word, v in _PRAYER_WORDS.items() if word in text), None)
        if key is None:
            upcoming = await prayer_api.next_prayer(lat, lon, method=method)
            if upcoming["remaining_seconds"] <= 0:  # pragma: no cover - احتياطي
                upcoming = await prayer_api.next_prayer(lat, lon, method=method)
            body = (
                f"⏳ <b>الصلاة القادمة: {esc(upcoming['name'])}</b>\n"
                f"{esc(upcoming['remaining_text'])} (عند {esc(upcoming['time_text'])})\n"
                f"<i>{esc(upcoming['hijri_text'])}</i>"
            )
        else:
            data = await prayer_api.timings(lat, lon, method=method, tz=tz)
            time_value = data["timings"][key]
            hour, minute = prayer_api.split_time(time_value)
            zone = ZoneInfo(data["meta"]["timezone"] or tz or "UTC")
            now = datetime.now(zone)
            at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if at <= now:
                at += timedelta(days=1)
            seconds = int((at - now).total_seconds())
            label_ar = dict(prayer_api.PRAYER_ORDER)[key]
            body = (
                f"⏳ <b>بقي لصلاة {esc(label_ar)}</b>\n"
                f"{esc(prayer_api.humanize_remaining(seconds))} "
                f"(عند {esc(prayer_api.format_time_ar(time_value))})\n"
                f"<i>{esc(data['date_text'])} — {esc(data['hijri_text'])}</i>"
            )

        reply = AgentReply(
            text=body,
            agent=self.name,
            buttons=[
                [
                    Button("المواقيت 🕌", "ag:prayer:timings"),
                    Button("تحديث 🔄", "ag:prayer:next"),
                ]
            ],
        )
        reply.with_source(SOURCE_TIMINGS)
        return reply

    # ── سلوك 5: شهر كامل ───────────────────────────────────────────
    async def _monthly(self, request: AgentRequest) -> AgentReply:
        coords = await self._resolve(request, self._extract_city(request.text))
        if coords is None:
            return self._city_not_found(request.text)
        lat, lon, _tz, label, method = coords

        days = await prayer_api.monthly(lat, lon, method=method)
        header_cols = ["فجر", "شروق", "ظهر", "عصر", "مغرب", "عشاء"]
        header = "يوم  " + " ".join(f"{c:>8}" for c in header_cols)
        lines = [header]
        for day in days:
            cells = [
                prayer_api.format_time_ar(day["timings"][key])
                for key in ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
                if key in day["timings"]
            ]
            lines.append(f"{to_arabic_digits(day['day']):>3}  " + " ".join(f"{c:>8}" for c in cells))
        table = "\n".join(lines)

        body = (
            f"📅 <b>مواقيت الشهر — {esc(label)}</b>\n"
            f"<i>{esc(days[0]['short_date'])} — {esc(days[-1]['short_date'])}</i>\n\n"
            f"<code>{esc(table)}</code>\n\n"
            f"<i>التقدير حسب طريقة الحساب المعتمدة.</i>"
        )
        reply = AgentReply(
            text=clean(body),
            agent=self.name,
            buttons=[
                [
                    Button("تحديث 🔄", "ag:prayer:monthly"),
                    Button("مواقيت اليوم 🕌", "ag:prayer:timings"),
                ]
            ],
        )
        reply.with_source(SOURCE_TIMINGS).with_source(SOURCE_HIJRI)
        return reply

    # ── أدوات مساعدة ───────────────────────────────────────────────
    def _error(self, message: str) -> AgentReply:
        reply = AgentReply(
            text=f"{message} حاول مرّة أخرى بعد قليل.",
            agent=self.name,
            buttons=[[Button("إعادة المحاولة 🔄", "ag:prayer:timings")]],
        )
        return reply

    def _city_not_found(self, city: str | None) -> AgentReply:
        name = esc(city or "")
        return AgentReply(
            text=(
                f"🔎 لم أجد المدينة «{name}». "
                "تأكّد من كتابة الاسم بشكل صحيح، أو استخدم المدينة المحفوظة في ملفك."
            ),
            agent=self.name,
            buttons=[[Button("مواقيت مدينتي 🕌", "ag:prayer:timings")]],
        )

    @staticmethod
    def _extract_city(raw: str) -> str | None:
        """يستخرج اسم مدينة صريحاً من النص («مواقيت الصلاة في وهران»)."""
        normalized = normalize_arabic(raw or "")
        if not normalized:
            return None
        match = re.search(r"(?:في|مدينه|بمدينه)\s+(.+)$", normalized)
        if not match:
            return None
        words = [w for w in match.group(1).split() if w and w not in _CITY_STOPWORDS]
        city = " ".join(words).strip()
        return city or None

    async def _resolve(
        self, request: AgentRequest, city: str | None
    ) -> tuple[float, float, str, str, int] | None:
        """يعيد (lat, lon, tz, label, method) — ويحفظ المدينة الجديدة إن وُجدت."""
        user = request.user
        if not city:
            return user.lat, user.lon, user.tz, user.city, user.method

        coords = await prayer_api.find_city_coords(city)
        if not coords:
            return None

        lat = float(coords["latitude"])
        lon = float(coords["longitude"])
        tz = coords.get("timezone") or user.tz
        label = coords.get("name") or city
        try:  # حفظ أفضل جهد: لا يُفشل الردّ إن تعذّر الحفظ.
            await db.update_user(
                request.chat_id,
                city=label,
                country=coords.get("country") or user.country,
                lat=lat,
                lon=lon,
                tz=tz,
            )
        except Exception:
            pass
        return lat, lon, tz, label, user.method
