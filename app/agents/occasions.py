"""وكيل المناسبات: يحسب المناسبات الهجرية القادمة بالأيام المتبقّية.

الحساب **تقريبي حسابي** (متوسط أطوال الأشهر الهجرية) وقد يخالف الرؤية بيوم،
وهذا مذكور صراحةً في الردّ.
"""
from __future__ import annotations

import json
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path

from ..models import AgentReply, AgentRequest, Button
from ..services import prayer_api
from ..services.prayer_api import PrayerAPIError
from ..text import bullet_list, clean, esc, to_arabic_digits
from .base import BaseAgent

SOURCE_HIJRI = "تقويم أم القرى (تحويل هجري)"
SOURCE_TIMINGS = "AlAdhan API — حساب المواقيت"
DISCLAIMER = "التقدير حسابي وقد يخالف الرؤية بيوم."

HIJRI_MONTHS = [
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

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "occasions.json"


def load_occasions(path: Path | None = None) -> list[dict]:
    """يحمّل ملف المناسبات (قائمة مناسبات هجرية)."""
    target = path or _DATA_PATH
    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - ملف تالف
        return []
    return [item for item in data if isinstance(item, dict)]


_OCCASIONS: list[dict] = load_occasions()


# ── تقويم هجري تقريبي ──────────────────────────────────────────────


def is_leap_year(year: int) -> bool:
    """سنة كبيسة هجرياً وفق دورة الـ30 سنة (تُضاف لذو الحجة)."""
    return ((year * 11 + 14) % 30) < 11


def month_length(month: int, year: int) -> int:
    if month == 12:
        return 30 if is_leap_year(year) else 29
    return 30 if month % 2 == 1 else 29


def hijri_to_absolute(year: int, month: int, day: int) -> int:
    """رقم يوم مطلق تقريبي — للمقارنة وحساب الفروق فقط."""
    total = (year - 1) * 354
    total += sum(month_length(m, year) for m in range(1, month))
    return total + day


def _format_gregorian(day: date_cls) -> str:
    month_ar = AR_GREGORIAN_MONTHS[day.month - 1]
    return f"{to_arabic_digits(day.day)} {month_ar} {to_arabic_digits(day.year)}"


def humanize_days(days: int) -> str:
    if days <= 0:
        return "اليوم"
    if days == 1:
        return "غداً"
    if days == 2:
        return "بعد يومين"
    if 3 <= days <= 10:
        return f"بعد {to_arabic_digits(days)} أيام"
    return f"بعد {to_arabic_digits(days)} يوماً"


# ── الحساب النقي ───────────────────────────────────────────────────


def upcoming(
    now_hijri: dict,
    limit: int = 5,
    *,
    occasions: list[dict] | None = None,
    today: date_cls | None = None,
) -> list[dict]:
    """أقرب المناسبات مرتّبة بالأيام المتبقّية.

    now_hijri: {"year": int, "month": int, "day": int}
    دالة نقيّة (لا شبكة ولا وقت نظام) لتكون قابلة للاختبار مباشرة.
    """
    items = occasions if occasions is not None else _OCCASIONS
    now_abs = hijri_to_absolute(
        int(now_hijri["year"]), int(now_hijri["month"]), int(now_hijri["day"])
    )

    found: list[dict] = []
    for occasion in items:
        month = int(occasion.get("month") or 0)
        day = int(occasion.get("day") or 0)
        if not (1 <= month <= 12 and 1 <= day <= 30):
            continue
        for year in (int(now_hijri["year"]), int(now_hijri["year"]) + 1):
            event_abs = hijri_to_absolute(year, month, day)
            remaining = event_abs - now_abs
            if remaining < 0:
                continue
            gregorian = today + timedelta(days=remaining) if today else None
            found.append(
                {
                    "id": occasion.get("id"),
                    "title": occasion.get("title") or "",
                    "kind": occasion.get("kind") or "",
                    "hint": occasion.get("hint") or "",
                    "month": month,
                    "day": day,
                    "hijri_year": year,
                    "remaining_days": remaining,
                    "hijri_text": f"{to_arabic_digits(day)} "
                    f"{HIJRI_MONTHS[month - 1]} {to_arabic_digits(year)}هـ",
                    "gregorian": gregorian,
                    "date_text": _format_gregorian(gregorian) if gregorian else "",
                }
            )
            break

    found.sort(key=lambda item: item["remaining_days"])
    return found[: max(0, int(limit))]


class OccasionsAgent(BaseAgent):
    name = "occasions"
    title = "وكيل المناسبات"
    description = "المناسبات الإسلامية وتواريخها الهجرية."
    priority = 60
    keywords = [
        "مناسبة",
        "مناسبات",
        "عيد",
        "الأضحى",
        "الاضحى",
        "الفطر",
        "رمضان",
        "عرفة",
        "عاشوراء",
        "محرم",
        "شعبان",
        "رجب",
        "ليلة القدر",
        "هجرة",
        "تقويم",
    ]
    examples = [
        "ما أقرب مناسبة؟",
        "متى عيد الأضحى",
        "متى رمضان",
        "المناسبات القادمة",
    ]

    async def handle(self, request: AgentRequest) -> AgentReply:
        action = (request.args or "").strip().lower()
        try:
            hijri = await prayer_api.hijri()
        except PrayerAPIError:
            return AgentReply(
                text="⚠️ تعذّر جلب التاريخ الهجري الآن. حاول مرّة أخرى بعد قليل.",
                agent=self.name,
                buttons=[[Button("إعادة المحاولة 🔄", "ag:occasions:list")]],
            )
        except Exception:  # دفاع نهائي
            return AgentReply(
                text="⚠️ حدث خطأ غير متوقّع أثناء حساب المناسبات.",
                agent=self.name,
                buttons=[[Button("إعادة المحاولة 🔄", "ag:occasions:list")]],
            )

        now_hijri = {
            "year": int(hijri.get("year") or 0),
            "month": int(hijri.get("month_number") or 0),
            "day": int(hijri.get("day") or 0),
        }
        today = date_cls.today()
        limit = 1 if action == "next" else 5
        items = upcoming(now_hijri, limit, today=today)

        if not items:
            return AgentReply(
                text="لم أستطع تحديد مناسبات قادمة.",
                agent=self.name,
                buttons=[[Button("إعادة المحاولة 🔄", "ag:occasions:list")]],
            )

        lines: list[str] = []
        for item in items:
            when = humanize_days(item["remaining_days"])
            date_note = f" — {esc(item['date_text'])}" if item["date_text"] else ""
            lines.append(f"<b>{esc(item['title'])}</b>\n   {esc(when)}{date_note}")

        friday_note = self._friday_note(today)
        heading = "⏭ <b>أقرب مناسبة</b>" if action == "next" else "📿 <b>المناسبات القادمة</b>"
        body = (
            f"{heading}\n"
            f"<i>التاريخ الهجري اليوم: {esc(hijri.get('text') or '')}</i>\n\n"
            f"{bullet_list(lines)}\n"
        )
        if friday_note:
            body += f"\n{friday_note}\n"
        body += f"\n<i>⚠️ {esc(DISCLAIMER)}</i>"

        reply = AgentReply(
            text=clean(body),
            agent=self.name,
            buttons=[
                [
                    Button("أقرب مناسبة ⏭", "ag:occasions:next"),
                    Button("كل المناسبات 📿", "ag:occasions:list"),
                ],
                [Button("التاريخ الهجري 📅", "ag:prayer:hijri")],
            ],
        )
        reply.with_source(SOURCE_HIJRI).with_source(SOURCE_TIMINGS)
        return reply

    @staticmethod
    def _friday_note(today: date_cls) -> str:
        days = (4 - today.weekday()) % 7  # 4 = الجمعة
        if days == 0:
            return "🕌 اليوم الجمعة — لا تنسَ الصلاة على النبي ﷺ."
        return f"🕌 الجمعة القادمة {humanize_days(days)}."
