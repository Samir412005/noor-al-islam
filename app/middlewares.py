"""وسطاء aiogram: تحميل ملفّ المستخدم قبل كل معالج."""
from __future__ import annotations

import logging
import time
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


# حدّ البطء: ما تجاوزه يُسجَّل تحذيراً (للتشخيص المبكّر)
SLOW_THRESHOLD = 2.0
NOTICE_THRESHOLD = 0.8


class TimingMiddleware(BaseMiddleware):
    """يقيس زمن معالجة كل تحديث — أساس أيّ تشخيص بطء.

    يُسجَّل التحذير فقط عند تجاوز الحدّ، حتى يبقى السجلّ نظيفاً.
    """

    def __init__(self, slow: float = SLOW_THRESHOLD, notice: float = NOTICE_THRESHOLD) -> None:
        self.slow = slow
        self.notice = notice
        self.total = 0
        self.count = 0
        self.worst = 0.0

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        start = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            elapsed = time.perf_counter() - start
            self.total += elapsed
            self.count += 1
            self.worst = max(self.worst, elapsed)
            label = type(event).__name__
            if elapsed >= self.slow:
                log.warning("🐌 بطء: %s استغرق %.2f ثانية", label, elapsed)
            elif elapsed >= self.notice:
                log.info("⏱️ %s: %.2f ثانية", label, elapsed)
            else:
                log.debug("%s: %.3f ثانية", label, elapsed)

    def stats(self) -> dict[str, float]:
        average = self.total / self.count if self.count else 0.0
        return {"count": self.count, "avg": round(average, 3), "worst": round(self.worst, 3)}


timing = TimingMiddleware()
