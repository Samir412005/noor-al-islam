"""تجهيز البوت: الأوامر والمُوزِّع (Dispatcher) والوسطاء."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from .config import settings
from .handlers import setup_routers
from .middlewares import ErrorMiddleware, UserMiddleware

log = logging.getLogger(__name__)

COMMANDS: list[tuple[str, str]] = [
    ("start", "رسالة البداية 🕌"),
    ("menu", "القائمة الرئيسية"),
    ("quran", "📖 وكيل القرآن"),
    ("hadith", "📜 وكيل الحديث"),
    ("adhkar", "📿 وكيل الأذكار"),
    ("prayer", "🕌 المواقيت والقبلة"),
    ("occasions", "🌙 المناسبات القادمة"),
    ("ask", "🧠 اسأل وكيل العلم"),
    ("city", "📍 تحديد مدينتك"),
    ("settings", "⚙️ الإعدادات"),
    ("help", "📘 المساعدة"),
    ("about", "ℹ️ عن البوت"),
]


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    # الوسيط الخارجي للخطأ يغلّف الجميع، ثم وسيط المستخدم
    dp.message.outer_middleware(ErrorMiddleware())
    dp.callback_query.outer_middleware(ErrorMiddleware())
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())

    setup_routers(dp)
    return dp


async def set_commands(bot: Bot) -> None:
    """يضبط قائمة الأوامر في واجهة تيليجرام (مع السقوط الصامت عند الفشل)."""
    try:
        await bot.set_my_commands(
            [BotCommand(command=name, description=desc) for name, desc in COMMANDS],
            scope=BotCommandScopeAllPrivateChats(),
        )
        log.info("تم تحديث قائمة الأوامر (%d أمراً)", len(COMMANDS))
    except Exception as exc:  # pragma: no cover
        log.warning("set_my_commands failed: %s", exc)


async def set_profile(bot: Bot) -> None:
    """يضبط الوصف والوصف المختصر — مطابق لهوية البوت."""
    description = (
        "🕌 نور الإسلام — رفيقك اليومي للقرآن والحديث والأذكار ومواقيت الصلاة.\n"
        "ستة وكلاء: القرآن، الحديث، الأذكار، المواقيت، المناسبات، ووكيل العلم.\n"
        "كل آية وحديث بمصدره — ولا فتوى ولا نصّ بلا مرجع."
    )
    short = "قرآن · حديث · أذكار · مواقيت — بمصادرها 🕌"
    try:
        await bot.set_my_description(description=description)
        await bot.set_my_short_description(short_description=short)
    except Exception as exc:  # pragma: no cover
        log.warning("set profile failed: %s", exc)


async def close_clients() -> None:
    """إغلاق الموارد المشتركة."""
    from .db import db
    from .llm import llm
    from .services.http import http

    await db.close()
    await llm.aclose()
    await http.aclose()


__all__ = ["create_dispatcher", "set_commands", "set_profile", "close_clients", "COMMANDS", "settings"]
