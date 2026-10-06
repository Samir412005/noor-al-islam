"""معالج ضغطات أزرار الوكلاء (ag:<agent>:…)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from ..models import UserProfile
from . import callbacks as cb
from .core import process
from .render import send_reply

log = logging.getLogger(__name__)
router = Router(name="agent_callbacks")


@router.callback_query(F.data.startswith("ag:"))
async def on_agent_callback(callback: CallbackQuery, user: UserProfile) -> None:
    data = callback.data or ""
    await callback.answer()

    special = cb.special_reply(data, user)
    if special is not None and callback.message is not None:
        await send_reply(callback.message, special)
        return

    request = cb.build_request(data, callback.message.chat.id if callback.message else user.chat_id, user)
    if request is None:
        if callback.message:
            await callback.message.answer("⚠️ هذا الزرّ لم يعد صالحاً. استعمل /menu")
        return

    reply = await process(request)
    if callback.message is not None:
        await send_reply(callback.message, reply)
