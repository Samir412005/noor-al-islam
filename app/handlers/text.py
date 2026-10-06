"""النصّ الحرّ: آخر ملاذ قبل الرسالة التوضيحية."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from ..models import UserProfile
from .core import process_text
from .render import send_reply

log = logging.getLogger(__name__)
router = Router(name="text")

MIN_LENGTH = 2


@router.message(F.text)
async def on_text(message: Message, user: UserProfile) -> None:
    text = (message.text or "").strip()
    if len(text) < MIN_LENGTH:
        await message.answer("اكتب سؤالك أو استعمل /menu 🙂")
        return
    reply = await process_text(text, message.chat.id, user)
    await send_reply(message, reply)


@router.message(F.voice | F.audio | F.photo | F.document | F.video)
async def on_unsupported(message: Message, user: UserProfile) -> None:
    await message.answer(
        "أعمل بالنصوص فقط 🙏\nاكتب طلبك، مثال: «أذكار الصباح» أو «تفسير 2:255».",
    )
