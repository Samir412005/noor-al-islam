"""نقطة الدخول: `python -m app.main`.

يدعم وضعين:
- `RUN_MODE=polling`  (افتراضي، مناسب للتطوير والخوادم الصغيرة)
- `RUN_MODE=webhook`  (موصى به في الإنتاج: أقلّ استهلاكاً وردّ أسرع)
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from .bot import close_clients, create_dispatcher, set_commands, set_profile
from .config import settings
from .db import db
from .scheduler import run_scheduler


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def validate_settings() -> None:
    if not settings.bot_token or ":" not in settings.bot_token:
        sys.exit(
            "❌ BOT_TOKEN غير صالح. ضع التوكن في ملف .env أو متغيّرات البيئة.\n"
            "   مثال: BOT_TOKEN=123456789:AA... "
        )
    if settings.run_mode == "webhook":
        if not settings.webhook_base_url:
            sys.exit("❌ RUN_MODE=webhook يتطلّب WEBHOOK_BASE_URL (رابط https عام).")
        if not settings.webhook_secret:
            sys.exit(
                "❌ RUN_MODE=webhook يتطلّب WEBHOOK_SECRET (سلسلة عشوائية طويلة).\n"
                "   بدونها يستطيع أي طرف إرسال تحديثات مصنوعة يدوياً إلى نقطة الويب هوك."
            )
    if settings.run_mode not in {"polling", "webhook"}:
        sys.exit("❌ RUN_MODE يجب أن يكون polling أو webhook.")


async def prepare(bot: Bot) -> None:
    await db.connect()
    await set_commands(bot)
    await set_profile(bot)


async def run_polling(bot: Bot, dp: Dispatcher, scheduler_task: asyncio.Task | None) -> None:
    logging.info("▶️ تشغيل البوت بوضع polling …")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def run_webhook(bot: Bot, dp: Dispatcher) -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    logging.info("▶️ تشغيل البوت بوضع webhook على %s", settings.webhook_url)
    await bot.set_webhook(
        settings.webhook_url,
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=True,
        max_connections=40,
    )

    app = web.Application()
    handler = SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=settings.webhook_secret or None
    )
    handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    logging.info("✅ الخدمة تستمع على المنفذ %d", settings.port)
    await asyncio.Event().wait()  # يبقى يعمل حتى الإيقاف


async def main() -> None:
    setup_logging()
    validate_settings()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=None))
    dp = create_dispatcher()
    scheduler_task: asyncio.Task | None = None

    try:
        await prepare(bot)
        me = await bot.get_me()
        logging.info("🤖 @%s (%s) — LLM: %s", me.username, me.first_name,
                     "مفعّل" if settings.llm_enabled else "غير مفعّل")

        if settings.daily_push:
            scheduler_task = asyncio.create_task(run_scheduler(bot), name="daily-push")

        if settings.run_mode == "webhook":
            await run_webhook(bot, dp)
        else:
            await run_polling(bot, dp, scheduler_task)
    finally:
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
        await close_clients()
        await bot.session.close()
        logging.info("👋 تم إيقاف البوت نظيفاً")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
