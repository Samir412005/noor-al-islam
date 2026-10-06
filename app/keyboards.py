"""لوحات المفاتيح (Inline) وبناء أزرار تيليجرام."""
from __future__ import annotations

from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .models import Button

AGENT_LABELS: dict[str, str] = {
    "quran": "📖 القرآن",
    "hadith": "📜 الحديث",
    "adhkar": "📿 الأذكار",
    "prayer": "🕌 المواقيت",
    "occasions": "🌙 المناسبات",
    "scholar": "🧠 اسأل",
}


def keyboard(rows: Iterable[Iterable[Button]]) -> InlineKeyboardMarkup | None:
    """يحوّل أزرارنا المحلية إلى لوحة تيليجرام."""
    final: list[list[InlineKeyboardButton]] = []
    for row in rows:
        cells: list[InlineKeyboardButton] = []
        for button in row:
            if not button or not button.text:
                continue
            cells.append(InlineKeyboardButton(text=button.text, callback_data=button.callback_data))
        if cells:
            final.append(cells)
    return InlineKeyboardMarkup(inline_keyboard=final) if final else None


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [Button("📖 وكيل القرآن", "menu:quran"), Button("📜 وكيل الحديث", "menu:hadith")],
        [Button("📿 وكيل الأذكار", "menu:adhkar"), Button("🕌 وكيل المواقيت", "menu:prayer")],
        [Button("🌙 المناسبات", "menu:occasions"), Button("🧠 وكيل العلم", "menu:scholar")],
        [Button("⚙️ الإعدادات", "menu:settings"), Button("ℹ️ عن البوت", "menu:about")],
    ]
    return keyboard(rows)  # type: ignore[return-value]


def settings_menu(city: str, method: int, reciter: str, daily: bool) -> InlineKeyboardMarkup:
    rows = [
        [Button(f"📍 المدينة: {city}", "menu:setcity")],
        [Button("الحساب: تغيير الطريقة", "menu:method")],
        [Button("القارئ: تغيير الصوت", "menu:reciter")],
        [Button(("🔔 الإشعارات: مفعّلة" if daily else "🔕 الإشعارات: موقوفة"), "set:push")],
        [Button("⬅️ الرئيسية", "menu:main")],
    ]
    return keyboard(rows)  # type: ignore[return-value]


def back_menu(target: str = "menu:main", label: str = "⬅️ الرئيسية") -> InlineKeyboardMarkup:
    return keyboard([[Button(label, target)]])  # type: ignore[return-value]


def examples_menu(agent: str, examples: list[str]) -> InlineKeyboardMarkup:
    rows = [[Button(f"▫️ {text[:40]}", f"ex:{agent}:{index}")] for index, text in enumerate(examples[:6])]
    rows.append([Button("▶️ ابدأ", f"ex:{agent}:0"), Button("⬅️ الرئيسية", "menu:main")])
    return keyboard(rows)  # type: ignore[return-value]
