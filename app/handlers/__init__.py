"""تجميع معالجات الرسائل في الراوتر الرئيسي."""
from __future__ import annotations

from aiogram import Dispatcher

from . import admin, agent_callbacks, commands, menu, text


def setup_routers(dp: Dispatcher) -> None:
    """الترتيب مقصود: الأوامر ← القوائم ← أزرار الوكلاء ← النصّ الحرّ."""
    dp.include_router(commands.router)
    dp.include_router(admin.router)
    dp.include_router(menu.router)
    dp.include_router(agent_callbacks.router)
    dp.include_router(text.router)


__all__ = ["setup_routers"]
