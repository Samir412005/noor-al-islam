"""أوامر المشرف: /stats للجميع، والبثّ للمشرفين فقط."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import settings
from ..db import db
from ..models import UserProfile
from ..text import to_arabic_digits

log = logging.getLogger(__name__)
router = Router(name="admin")


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(Command("stats", "إحصاء"))
async def cmd_stats(message: Message, user: UserProfile) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("🔒 هذا الأمر مخصّص لمشرفي البوت.")
        return
    stats = await db.stats()
    await message.answer(
        "<b>📊 إحصاءات البوت</b>\n\n"
        f"• المستخدمون: <b>{to_arabic_digits(stats['users'])}</b>\n"
        f"• المشتركون في الإشعارات: <b>{to_arabic_digits(stats['subscribed'])}</b>\n"
        f"• الرسائل المخزّنة: <b>{to_arabic_digits(stats['messages'])}</b>"
    )


@router.message(Command("broadcast", "بث"))
async def cmd_broadcast(message: Message, bot: Bot, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("🔒 هذا الأمر مخصّص لمشرفي البوت.")
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer("الاستعمال: <code>/broadcast نصّ الرسالة</code>")
        return

    recipients = await db.subscribers()
    await message.answer(f"⏳ جارٍ الإرسال إلى {to_arabic_digits(len(recipients))} مستخدماً…")
    sent = failed = 0
    for index, user in enumerate(recipients, start=1):
        try:
            await bot.send_message(user.chat_id, text)
            sent += 1
        except Exception:  # pragma: no cover - يعتمد على تيليجرام
            failed += 1
        if index % 25 == 0:
            await asyncio.sleep(1)  # احترام حدود تيليجرام
    await message.answer(
        f"✅ تمّ: {to_arabic_digits(sent)} · فشل: {to_arabic_digits(failed)}"
    )
