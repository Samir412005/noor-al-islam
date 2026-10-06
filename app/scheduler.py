"""الإشعارات المجدولة: أذكار الصباح والمساء، آية اليوم، ومواقيت اليوم.

الحلقة تعمل دائماً (تكلفتها سطور سجلّ كل دقيقة) والقرار لكل مستخدم على حدة:
تُرسل لمن فعّل `daily_push` في إعداداته فقط. و`DAILY_PUSH` في البيئة تعني
«هل الإشعارات مفعّلة افتراضياً للمستخدم الجديد؟».

تُمنع التكرار بجدول `push_state`، وتُطوى الإشعارات التي فاتتها نافذتها (٤٥ دقيقة).
يجب أن تكون العملية دائمة (polling أو webhook) لتصل الإشعارات.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from .agents import get_agent
from .config import settings
from .db import db
from .handlers.core import build_request, process
from .keyboards import keyboard
from .models import AgentReply, Button, UserProfile
from .services import prayer_api
from .text import esc

log = logging.getLogger(__name__)

# (النوع, الساعة, الدقيقة, التفضيل المطلوب)
SCHEDULE: tuple[tuple[str, int, int, str], ...] = (
    ("prayer", 5, 0, "push_prayer"),
    ("adhkar_morning", 6, 30, "push_adhkar"),
    ("quran", 8, 0, "push_quran"),
    ("adhkar_evening", 18, 30, "push_adhkar"),
)

CHECK_INTERVAL = 60  # ثانية
# نافذة الإرسال: إن فات وقت الإشعار بأكثر من هذا (توقّف/إقلاع متأخر) لا نُزعج
# المستخدم برسائل قديمة — نطويها بصمت. وتمنع أيضاً انفجار الإشعارات عند الإقلاع.
WINDOW_MINUTES = 45


def _now(tz: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz))
    except Exception:  # pragma: no cover - منطقة زمنية غير صالحة
        return datetime.now(ZoneInfo(settings.default_tz))


async def _payload(kind: str, user: UserProfile) -> AgentReply | None:
    """يبني محتوى الإشعار بلا تكرار منطق الوكلاء."""
    prompts = {
        "quran": "آية اليوم",
        "adhkar_morning": "أذكار الصباح",
        "adhkar_evening": "أذكار المساء",
        "prayer": "مواقيت الصلاة",
    }
    agent_name = {"quran": "quran", "adhkar_morning": "adhkar", "adhkar_evening": "adhkar", "prayer": "prayer"}[kind]

    request = build_request(prompts[kind], user.chat_id, user, raw_command=f"/{agent_name}")
    try:
        reply = await process(request)
    except Exception:  # pragma: no cover
        log.exception("daily payload failed for %s", kind)
        return None
    return reply


async def _send_daily(bot: Bot) -> None:
    subscribers = await db.subscribers()
    if not subscribers:
        return

    for user in subscribers:
        if not user.daily_push:
            continue
        now = _now(user.tz or settings.default_tz)
        day = now.strftime("%Y-%m-%d")

        for kind, hour, minute, pref in SCHEDULE:
            if not getattr(user, pref, True):
                continue
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < scheduled:
                continue
            if await db.push_already_sent(user.chat_id, kind, day):
                continue
            if now - scheduled > timedelta(minutes=WINDOW_MINUTES):
                # فات وقتها (توقّف/إقلاع متأخر) — نطويها بلا إزعاج
                await db.mark_push_sent(user.chat_id, kind, day)
                log.info("تخطّي إشعار فاتته النافذة: %s (%s)", kind, user.chat_id)
                continue

            reply = await _payload(kind, user)
            if reply is None:
                # لا نُسجّله: فشل بناء المحتوى لا يجب أن يُلغي إشعار اليوم
                continue
            try:
                await bot.send_message(
                    user.chat_id,
                    f"<b>🔔 إشعارك اليومي</b>\n\n{reply.text}",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=keyboard(
                        [[Button("⚙️ الإشعارات", "menu:settings"), Button("🕌 القائمة", "menu:main")]]
                    ),
                )
            except Exception as exc:  # pragma: no cover - مستخدم حجب البوت مثلاً
                log.info("push to %s failed: %s", user.chat_id, exc)
                continue
            await db.mark_push_sent(user.chat_id, kind, day)
            await asyncio.sleep(0.2)  # احترام حدود تيليجرام


async def run_scheduler(bot: Bot) -> None:
    """حلقة دائمة — تُلغى عند إيقاف البوت."""
    log.info(
        "بدأت حلقة الإشعارات المجدولة (كل %d ثانية) — الافتراضي للمستخدم الجديد: %s",
        CHECK_INTERVAL,
        "مفعّل" if settings.daily_push else "موقوف",
    )
    while True:
        try:
            await _send_daily(bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            log.exception("scheduler iteration failed")
        await asyncio.sleep(CHECK_INTERVAL)


__all__ = ["run_scheduler", "SCHEDULE", "_send_daily", "_payload", "esc"]
