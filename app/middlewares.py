"""وسطاء aiogram: تحميل ملفّ المستخدم قبل كل معالج."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .db import db

log = logging.getLogger(__name__)


def _identify(event: TelegramObject) -> tuple[int | None, str]:
    """يستخرج (chat_id, الاسم الأول) من رسالة أو ضغطة زر."""
    if isinstance(event, Message):
        return event.chat.id, (event.from_user.first_name if event.from_user else "")
    if isinstance(event, CallbackQuery):
        chat = event.message.chat.id if event.message else (event.from_user.id if event.from_user else None)
        return chat, (event.from_user.first_name if event.from_user else "")
    return None, ""


class UserMiddleware(BaseMiddleware):
    """يحقن `user: UserProfile` في بيانات كل معالج."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id, first_name = _identify(event)
        if chat_id is not None:
            try:
                data["user"] = await db.get_user(chat_id, first_name)
            except Exception:  # pragma: no cover - لا نُسقط المعالجة بسبب قاعدة البيانات
                log.exception("failed to load user %s", chat_id)
        return await handler(event, data)


class ErrorMiddleware(BaseMiddleware):
    """يمنع تسرّب أي خطأ إلى المستخدم كـtraceback."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            log.exception("handler error: %s", type(event).__name__)
            try:
                if isinstance(event, Message):
                    await event.answer("⚠️ حدث خطأ غير متوقّع. أعِد المحاولة.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ حدث خطأ غير متوقّع.", show_alert=False)
            except Exception:  # pragma: no cover
                pass
            return None
