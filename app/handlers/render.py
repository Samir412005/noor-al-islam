"""إرسال ردود الوكلاء إلى تيليجرام (تقطيع + صوت + أزرار)."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from ..keyboards import keyboard
from ..models import AgentReply
from ..text import chunk

log = logging.getLogger(__name__)


async def send_reply(
    message: Message,
    reply: AgentReply,
    *,
    bot: Bot | None = None,
    edit: bool = False,
) -> None:
    """يرسل ردّ الوكيل كرسالة أو أكثر، ثم الصوت إن وُجد."""
    markup = keyboard(reply.buttons)
    parse_mode = None if reply.parse_mode == "none" else reply.parse_mode
    parts = chunk(reply.text or "…")

    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        try:
            if edit and index == 0:
                await message.edit_text(
                    part,
                    parse_mode=parse_mode,
                    disable_web_page_preview=reply.disable_preview,
                    reply_markup=markup if last else None,
                )
            else:
                await message.answer(
                    part,
                    parse_mode=parse_mode,
                    disable_web_page_preview=reply.disable_preview,
                    reply_markup=markup if last else None,
                )
        except TelegramAPIError as exc:
            log.warning("send failed (%s) — إعادة الإرسال بلا تنسيق", exc)
            plain = part.replace("<b>", "").replace("</b>", "").replace("<i>", "")
            plain = plain.replace("</i>", "").replace("<code>", "").replace("</code>", "")
            await message.answer(plain, disable_web_page_preview=reply.disable_preview)

    if reply.audio_url:
        target = bot or message.bot
        if target is not None:
            try:
                await target.send_audio(
                    chat_id=message.chat.id,
                    audio=reply.audio_url,
                    title=reply.audio_title or "تلاوة",
                    caption=reply.audio_title,
                )
            except TelegramAPIError as exc:
                log.warning("audio send failed: %s", exc)
                try:
                    await message.answer(reply.audio_url, disable_web_page_preview=True)
                except TelegramAPIError:  # pragma: no cover
                    pass


async def replace_reply(callback_message: Message, reply: AgentReply, *, bot: Bot | None = None) -> None:
    """يعرض الرد مكان رسالة الزر عند الإمكان، وإلا يرسله جديداً."""
    try:
        await send_reply(callback_message, reply, bot=bot, edit=True)
    except TelegramAPIError:  # pragma: no cover
        await send_reply(callback_message, reply, bot=bot)
