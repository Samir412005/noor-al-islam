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


def enable_uvloop() -> bool:
    """uvloop يضاعف سرعة الحلقة غير المتزامنة (Linux). يسقط بصمت إن لم يتوفّر."""
    try:
        import uvloop

        uvloop.install()
        return True
    except Exception:  # pragma: no cover
        return False


async def preload() -> None:
    """يحمّل البيانات الثقيلة قبل أول رسالة — فلا يدفع أول مستخدم ثمن التحميل."""
    from .agents import adhkar as adhkar_agent
    from .services import quran_api

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        await asyncio.to_thread(quran_api.load_quran)
        await asyncio.to_thread(adhkar_agent.load_data)
        logging.info("📦 تحميل مسبق للبيانات: %.2f ثانية", loop.time() - started)
    except Exception as exc:  # pragma: no cover - لا نُسقط الإقلاع بسبب التحميل
        logging.warning("تعذّر التحميل المسبق: %s", exc)


async def preload_hadith() -> None:
    """يبني فهرس البحث لكتب الحديث الأساسية **من الكاش المحلي** عند الإقلاع.

    لا نُنزّل شيئاً هنا: إن غاب الكاش يُبنى الفهرس عند أول بحث (مع تنزيل خلفي).
    الهدف: أول بحث حديث للمستخدم يكون فورياً لا متأخّراً ثانيةً كاملةً.
    """
    from .services import hadith_api

    for key in ("nawawi", "bukhari", "muslim"):
        try:
            cache_path = hadith_api._cache_path(key)  # noqa: SLF001
            if not cache_path.exists():
                continue
            started = asyncio.get_running_loop().time()
            await hadith_api.load_book(key)
            await hadith_api._search_index(key)  # noqa: SLF001
            logging.info(
                "📚 فهرس الحديث جاهز: %s (%.2f ث)",
                hadith_api.COLLECTIONS.get(key, key),
                asyncio.get_running_loop().time() - started,
            )
        except Exception as exc:  # pragma: no cover - لا نُسقط الإقلاع
            logging.debug("تعذّر تجهيز فهرس %s: %s", key, exc)


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
    """الحدّ الأدنى قبل قبول أول رسالة: قاعدة البيانات فقط (سريع جداً)."""
    await db.connect()


async def warm_up(bot: Bot) -> None:
    """التحميل المسبق وتسجيل الأوامر/الوصف — **بعد** فتح المنفذ، في الخلفية.

    السبب: كل ثانية تأخير هنا تعني ألّا يستجيب الويب هوك، فيرى تيليجرام 502
    ويعيد المحاولة بتباطؤ — وهو ما يشعر المستخدم بأنّ البوت «يطول».
    """
    try:
        await preload()
        await preload_hadith()
    except Exception:  # pragma: no cover
        logging.exception("فشل التحميل المسبق")
    try:
        await set_commands(bot)
        await set_profile(bot)
    except Exception:  # pragma: no cover
        logging.warning("تعذّر تحديث الأوامر/الوصف")


def start_warm_up(bot: Bot) -> None:
    asyncio.create_task(warm_up(bot), name="warm-up")


async def run_polling(bot: Bot, dp: Dispatcher, scheduler_task: asyncio.Task | None) -> None:
    logging.info("▶️ تشغيل البوت بوضع polling …")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def build_web_app(dp: Dispatcher, bot: Bot) -> "web.Application":
    """يبني تطبيق aiohttp: نقطة الويب هوك + فحص صحّة عامّ."""
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    app = web.Application()

    async def health(_request):
        """نقطة فحص عامّة (بلا أسرار) — للحارس ولقياس الأداء الحيّ."""
        from .middlewares import timing

        payload = {
            "status": "ok",
            "service": "noor-islam-bot",
            "updates": "webhook",
            "llm": settings.llm_enabled,
            "timing": timing.stats(),
        }
        try:
            info = await asyncio.wait_for(bot.get_webhook_info(), timeout=5.0)
            payload["pending"] = info.pending_update_count
            payload["webhook_ok"] = (info.url or "") == settings.webhook_url
            payload["last_error"] = info.last_error_message
        except Exception:  # pragma: no cover - الفحص لا يجب أن يفشل بسبب الشبكة
            payload["pending"] = None
        return web.json_response(payload)

    app.router.add_get("/health", health)
    handler = SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=settings.webhook_secret or None
    )
    handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)
    return app


async def run_webhook(bot: Bot, dp: Dispatcher) -> None:
    from aiohttp import web

    # ① نفتح المنفذ أولاً: البوت جاهز لاستقبال التحديثات فوراً
    app = build_web_app(dp, bot)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(
        runner, host="0.0.0.0", port=settings.port, reuse_port=settings.reuse_port or None
    )
    await site.start()
    logging.info(
        "✅ الخدمة تستمع على المنفذ %d (دور: %s · تقاسم المنفذ: %s)",
        settings.port,
        settings.worker_role,
        "نعم" if settings.reuse_port else "لا",
    )

    # ② ثم نضبط الويب هوك (نداء واحد سريع). لا نُسقط التحديثات المنتظرة أبداً.
    pending = 0
    try:
        info = await bot.get_webhook_info()
        pending = info.pending_update_count
    except Exception:  # pragma: no cover
        pass
    try:
        await bot.set_webhook(
            settings.webhook_url,
            secret_token=settings.webhook_secret or None,
            drop_pending_updates=False,
            max_connections=40,
        )
        logging.info("🔗 الويب هوك مضبوط على %s", settings.webhook_url)
    except Exception as exc:  # pragma: no cover
        logging.error("تعذّر ضبط الويب هوك: %s", exc)
    if pending:
        logging.info("📥 تحديثات منتظرة ستُعالَج: %d", pending)

    # ③ التحميل المسبق والأوامر/الوصف في الخلفية بلا حجب
    start_warm_up(bot)

    await asyncio.Event().wait()  # يبقى يعمل حتى الإيقاف


async def main() -> None:
    setup_logging()
    if enable_uvloop():
        logging.info("⚡ uvloop مفعّل (حلقة أسرع)")
    validate_settings()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=None))
    dp = create_dispatcher()
    scheduler_task: asyncio.Task | None = None

    try:
        await prepare(bot)
        me = await bot.get_me()
        logging.info("🤖 @%s (%s) — LLM: %s", me.username, me.first_name,
                     "مفعّل" if settings.llm_enabled else "غير مفعّل")

        # الحلقة تعمل دائماً: القرار لكل مستخدم (daily_push). لكن مع عمليتين
        # تتقاسمان المنفذ، تُشغّلها العملية الأساسية وحدها منعاً لتكرار الإشعارات.
        if settings.worker_role == "primary":
            scheduler_task = asyncio.create_task(run_scheduler(bot), name="daily-push")
        else:
            logging.info("ℹ️ دور %s: لا أُشغّل حلقة الإشعارات (تتولّاها العملية الأساسية)", settings.worker_role)

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
