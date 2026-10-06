"""بوت الحارس التفاعلي — واجهة تحكّم مستقلّة لحارس بوت نور الإسلام.

هذا بوت تيليجرام **منفصل تماماً** عن بوت نور الإسلام:
• يتكلّم عبر توكن خاصّ (`GUARDIAN_BOT_TOKEN` من @BotFather — خطوة يدوية واحدة).
• يعمل بوضع **polling** لا webhook، فلا يعتمد على منفذ البوت الهدف ولا على
  الويب هوك؛ يبقى قادراً على الردّ حتى لو كان البوت الهدف ساقطاً أو صامتاً.
• **مشرفون فقط**: من `config.admin_chat_ids`؛ غيرهم يُردّ عليه بلطف ويُتجاهل أمره.
• موارد خفيفة: لا يجري فحوصاً من تلقاء نفسه — الفحوص تُطلب بالأوامر فقط.

الأوامر: /start /status /checks /fix /restart /logs /incidents /mute /unmute /help

الاستيراد **لا يحتاج توكن**؛ التحقّق من التوكن يحدث في `main()` لا عند الاستيراد،
حتى تبقى الوحدة قابلة للاختبار والاستيراد في أي بيئة.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from app.text import chunk, esc

from . import checks, fixers
from .config import GuardianConfig, config
from .state import format_status, load_state, recent_incidents

log = logging.getLogger("guardian.bot")

#: أقصى طول لرسالة تيليجرام
TELEGRAM_LIMIT = 4096
#: أقصى عدد أسطر يطلبها /logs
MAX_LOG_LINES = 60
DEFAULT_LOG_LINES = 20
#: اسم زرّ الفحص السريع
CHECK_CALLBACK = "guardian:check"

ACCESS_DENIED = (
    "🔒 عذراً، هذه الواجهة مخصّصة لمشرفي «نور الإسلام» فقط.\n"
    "إن كنت مشرفاً، تأكّد من وجود معرّفك في ADMIN_IDS."
)

#: قائمة أوامر بوت الحارس الظاهرة في واجهة تيليجرام
BOT_COMMANDS: list[tuple[str, str]] = [
    ("start", "الترحيب وزرّ الفحص الفوري"),
    ("status", "تقرير الحالة الآن"),
    ("checks", "جدول الفحوصات المختصر"),
    ("fix", "إصلاح خلل: /fix <اسم الفحص>"),
    ("restart", "إعادة تشغيل البوت الهدف"),
    ("logs", "آخر سطور السجلّات: /logs [عدد]"),
    ("incidents", "آخر الحوادث"),
    ("mute", "كتم تنبيهات الحارس: /mute <دقائق>"),
    ("unmute", "إلغاء الكتم"),
    ("help", "قائمة الأوامر"),
]

DESCRIPTION = (
    "🛡️ حارس بوت نور الإسلام — واجهة مستقلة لمراقبة البوت وإصلاحه.\n"
    "يتحقّق من المشرف والويب هوك والمسار الكامل والقرص والذاكرة والقاعدة،\n"
    "ويُصلح الخلل بنقرة، ويبقى يعمل حتى لو سقط البوت الذي يحرسه."
)
SHORT_DESCRIPTION = "🛡️ مراقبة بوت نور الإسلام وإصلاحه بنقرة"

# ── جدول ترجمة أسماء الفحوصات (عربي ↔ إنجليزي) ──────────────────────
CHECK_LABELS_AR: dict[str, str] = {
    "supervisor": "المشرف",
    "keepalive": "الحراسة الدقيقة",
    "workers": "عمليات الخدمة",
    "local_health": "الصحة المحلية",
    "public_health": "الصحة العامة",
    "telegram": "الويب هوك",
    "pipeline": "المسار الكامل",
    "data_files": "ملفات البيانات",
    "disk": "القرص",
    "memory": "الذاكرة",
    "database": "قاعدة البيانات",
    "llm": "مزوّد الذكاء",
    "guardian_bot": "بوت الحارس",
}

#: كل فحص ← المُصلح المناسب له من `FIXERS`
CHECK_FIXERS: dict[str, str] = {
    "supervisor": "restart_supervisor",
    "keepalive": "restart_keepalive",
    "workers": "restart_workers",
    "local_health": "full_restart",
    "public_health": "sync_base_url",
    "telegram": "assert_webhook",
    "pipeline": "full_restart",
    "data_files": "refetch_data",
    "disk": "trim_logs",
    "memory": "restart_workers",
    "database": "repair_database",
    "llm": "disable_llm_on_failure",
    "guardian_bot": "check_token",  # يدوي: يتطلّب تدخّل المالك
}

#: مرادفات يكتبها المشرف (عربي/إنجليزي) للأسماء القياسية للفحوصات
CHECK_ALIASES: dict[str, str] = {
    "supervisor": "supervisor", "المشرف": "supervisor", "سوبرفايزر": "supervisor",
    "keepalive": "keepalive", "الحراسة": "keepalive", "الحراسه": "keepalive",
    "الحراسة الدقيقة": "keepalive", "الحراسه الدقيقه": "keepalive", "keepalive.sh": "keepalive",
    "workers": "workers", "العمليات": "workers", "عمال": "workers", "العمّال": "workers",
    "عمليات الخدمة": "workers", "عمليات": "workers",
    "local_health": "local_health", "الصحة المحلية": "local_health", "الفحص المحلي": "local_health",
    "public_health": "public_health", "الصحة العامة": "public_health", "العنوان العام": "public_health",
    "telegram": "telegram", "تيليجرام": "telegram", "تلجرام": "telegram", "الويب هوك": "telegram",
    "webhook": "telegram", "pipeline": "pipeline", "المسار": "pipeline", "المسار الكامل": "pipeline",
    "data_files": "data_files", "ملفات البيانات": "data_files", "البيانات": "data_files",
    "disk": "disk", "القرص": "disk", "المساحة": "disk", "مساحة": "disk",
    "memory": "memory", "الذاكرة": "memory", "ذاكرة": "memory",
    "database": "database", "قاعدة البيانات": "database", "القاعدة": "database", "db": "database",
    "llm": "llm", "الذكاء": "llm", "الذكاء الاصطناعي": "llm", "الذكاء الاصطناعى": "llm",
    "مزوّد الذكاء": "llm", "مزود الذكاء": "llm", "المزوّد": "llm",
    "guardian_bot": "guardian_bot", "بوت الحارس": "guardian_bot",
}

ALL_CHECK_NAMES = tuple(CHECK_LABELS_AR)


def _build_alias_index() -> dict[str, str]:
    from app.text import normalize_arabic

    index: dict[str, str] = {}
    for key, value in CHECK_ALIASES.items():
        index[key.strip().lower()] = value
        index.setdefault(normalize_arabic(key), value)
    return index


_ALIAS_INDEX = _build_alias_index()


# ── دوال نقية (قابلة للاختبار بلا شبكة) ────────────────────────────
def parse_command(text: str) -> tuple[str, str]:
    """يحلّل رسالة أمر: يُعيد (الأمر بلا شرطة وبلا @البوت، الوسيط).

    ``/fix@noor_guardian_bot المشرف`` ← ``("fix", "المشرف")``.
    أوامر غير صالحة تُعيد ``("", "")``.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return "", ""
    head, _, rest = raw.partition(" ")
    command = head[1:].split("@", 1)[0].strip().lower()
    return (command, rest.strip()) if command else ("", "")


def resolve_check_name(raw: str) -> str | None:
    """يحوّل ما كتبه المشرف (عربي/إنجليزي) إلى اسم الفحص القياسي أو None."""
    from app.text import normalize_arabic

    token = (raw or "").strip().lstrip("/").lower()
    if not token:
        return None
    if token in _ALIAS_INDEX:
        return _ALIAS_INDEX[token]
    normalized = normalize_arabic(token)
    return _ALIAS_INDEX.get(normalized)


def fixer_for_check(name: str) -> str | None:
    """اسم المُصلح المناسب لفحص قياسي (بلا تنفيذ)."""
    return CHECK_FIXERS.get((name or "").strip())


def is_admin(user_id: int | None, admins: Iterable[int] | None = None) -> bool:
    """هل المستخدم مشرف؟ (يقبل قائمة مشرفين محقونة للاختبار)."""
    if user_id is None:
        return False
    if admins is None:
        admins = config.admin_chat_ids
    try:
        return int(user_id) in {int(a) for a in admins}
    except (TypeError, ValueError):
        return False


def paginate(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """يقطّع نصاً إلى رسائل ≤ حدّ تيليجرام (يعتمد `app.text.chunk`)."""
    return chunk(text or "", limit=limit)


def format_checks_table(results: Iterable[Any]) -> str:
    """جدول مختصر: علامة الحالة + اسم الفحص (إنجليزي) + التسمية العربية."""
    lines = ["🧾 فحوصات الحارس:"]
    for result in results:
        mark = "✅" if result.ok else ("🛑" if result.severity == "critical" else "⚠️")
        label = CHECK_LABELS_AR.get(result.name, "")
        suffix = f" — {label}" if label else ""
        lines.append(f"{mark} {result.name}{suffix}")
    return "\n".join(lines)


def format_incidents(incidents: Iterable[dict]) -> str:
    """يُنسّق آخر الحوادث في نصّ عربي مقروء."""
    items = list(incidents)
    if not items:
        return "🕓 لا حوادث مسجّلة بعد."
    lines = ["🕓 آخر الحوادث:"]
    for item in items:
        when = time.strftime("%m-%d %H:%M", time.localtime(item.get("ts", 0)))
        name = item.get("name", "?")
        action = item.get("action") or item.get("outcome") or ""
        lines.append(f"• {when} — {name}: {action}".rstrip())
    return "\n".join(lines)


# ── الكتم (تنبيهات بوت الحارس فقط) ─────────────────────────────────
def mute_file(cfg: GuardianConfig | None = None) -> Path:
    """ملف بسيط يحوي وقت انتهاء الكتم (epoch). لا يمسّ إعدادات الحارس الأخرى."""
    return (cfg or config).state_dir / "bot_mute"


def set_mute(cfg: GuardianConfig | None = None, minutes: int = 60) -> int:
    """يكتم تنبيهات بوت الحارس مدّة دقائق ويُعيد وقت الانتهاء."""
    try:
        span = max(1, int(minutes))
    except (TypeError, ValueError):
        span = 60
    until = int(time.time()) + span * 60
    path = mute_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(until), encoding="utf-8")
    return until


def mute_until(cfg: GuardianConfig | None = None) -> int:
    """يقرأ وقت انتهاء الكتم؛ 0 إن لا كتم."""
    try:
        return int(mute_file(cfg).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def clear_mute(cfg: GuardianConfig | None = None) -> None:
    try:
        mute_file(cfg).unlink()
    except OSError:
        pass


def is_muted(cfg: GuardianConfig | None = None, now: float | None = None) -> bool:
    """هل التنبيهات الذاتية مكتومة الآن؟"""
    return mute_until(cfg) > (time.time() if now is None else now)


# ── قراءة السجلّات ──────────────────────────────────────────────────
LOG_FILES = ("bot-primary.log", "guardian.log")


def _tail_lines(path: Path, lines: int) -> list[str]:
    """يقرأ آخر n سطر من ملف كبير بلا تحميله كاملاً."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(8192, size)
                size -= step
                handle.seek(size)
                data = handle.read(step) + data
        return data.decode("utf-8", "ignore").splitlines()[-lines:]
    except OSError:
        return []


def read_log_excerpt(cfg: GuardianConfig, lines: int = DEFAULT_LOG_LINES) -> str:
    """آخر n سطر من سجلّ البوت الأساسي وسجلّ الحارس، مجمّعة في كتل موسومة."""
    try:
        count = int(lines)
    except (TypeError, ValueError):
        count = DEFAULT_LOG_LINES
    count = max(1, min(count, MAX_LOG_LINES))
    blocks: list[str] = []
    for name in LOG_FILES:
        tail = _tail_lines(cfg.target_dir / "logs" / name, count)
        if tail:
            blocks.append(f"— {name} —\n" + "\n".join(tail))
    return "\n\n".join(blocks) or "لا سجلّات بعد."


# ── وسطاء المشروع (حاجز أخطاء + تحقّق مشرف) ────────────────────────
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
        except Exception:  # noqa: BLE001 - لا نُسقط البوت بسبب أمر فاشل
            log.exception("خطأ في معالج بوت الحارس: %s", type(event).__name__)
            try:
                if isinstance(event, Message):
                    await event.answer("⚠️ حدث خطأ غير متوقّع في بوت الحارس.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ حدث خطأ غير متوقّع.", show_alert=False)
            except Exception:  # pragma: no cover
                pass
            return None


class GuardianAccessMiddleware(BaseMiddleware):
    """مشرفون فقط: يردّ بلطف على غير المشرفين ثم يتجاهل أمرهم."""

    def __init__(self, admins: Iterable[int]) -> None:
        self._admins = tuple(admins)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = getattr(user, "id", None)
        if not is_admin(user_id, self._admins):
            try:
                if isinstance(event, Message):
                    await event.answer(ACCESS_DENIED)
                elif isinstance(event, CallbackQuery):
                    await event.answer(ACCESS_DENIED, show_alert=True)
            except Exception:  # pragma: no cover
                pass
            return None
        return await handler(event, data)


# ── الإجراءات ──────────────────────────────────────────────────────
async def status_report(cfg: GuardianConfig) -> str:
    """يبني تقرير الحالة لحظياً (بنفس دوال الحارس)."""
    results = await asyncio.to_thread(checks.run_all, cfg)
    return format_status(results, load_state(cfg), cfg)


async def checks_report(cfg: GuardianConfig) -> str:
    results = await asyncio.to_thread(checks.run_all, cfg)
    return format_checks_table(results)


async def run_fixer(name: str, cfg: GuardianConfig) -> str:
    """يشغّل المُصلح المناسب لاسم فحص قياسي أو اسم مُصلح صريح."""
    check = resolve_check_name(name) or (name if name in CHECK_FIXERS else None)
    fixer_name = None
    if check:
        fixer_name = fixer_for_check(check)
    elif name in fixers.FIXERS:
        fixer_name, check = name, name
    if not fixer_name:
        return ""
    if fixer_name in fixers.MANUAL_ONLY:
        return f"ℹ️ «{check}» لا يُصلَح تلقائياً — يتطلّب تدخّل المالك."
    function = fixers.FIXERS.get(fixer_name)
    if function is None:
        return f"⚠️ لا يوجد مُصلح باسم «{fixer_name}»."
    try:
        outcome = await asyncio.to_thread(function, cfg)
    except Exception as exc:  # noqa: BLE001 - إصلاح فاشل لا يُسقط البوت
        outcome = f"فشل الإصلاح: {type(exc).__name__}"
    return f"🔧 <b>{esc(check)}</b>\n{esc(outcome)}"


async def notify_admins(bot: Bot, cfg: GuardianConfig, text: str) -> bool:
    """إرسال ذاتي (proactive) للتنبيهات — **يحترم الكتم**.

    أي رسالة يبدأها بوت الحارس من تلقاء نفسه تمرّ من هنا؛ إن كان الكتم سارياً
    فلا يُرسل شيء. ردود الأوامر لا تمرّ من هنا (تُحترم دائماً).
    """
    if is_muted(cfg):
        return False
    sent = False
    for chat_id in cfg.admin_chat_ids:
        try:
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            sent = True
        except Exception:  # noqa: BLE001 - مشرف واحد متعذّر لا يُسقط الباقين
            continue
    return sent


# ── الموجّه واليدلرز ───────────────────────────────────────────────
def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔍 افحص الآن", callback_data=CHECK_CALLBACK)]]
    )


async def _send(event: Message | CallbackQuery, text: str) -> None:
    """يرسل نصّاً مقسّماً على رسائل ≤ 4096 (بلا HTML — المتغيّرات غير موثوقة)."""
    for part in paginate(text):
        if isinstance(event, CallbackQuery):
            await event.message.answer(part)
        else:
            await event.answer(part)


def build_router(cfg: GuardianConfig) -> Router:
    router = Router(name="guardian")

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        text = (
            "🛡️ <b>حارس نور الإسلام</b>\n\n"
            "أنا بوت مستقلّ يراقب بوت نور الإسلام ويُصلحه، ويعمل حتى لو سقط هو.\n"
            "• /status تقرير الحالة الآن\n"
            "• /checks جدول الفحوصات\n"
            "• /fix إصلاح خلل\n"
            "• /help كل الأوامر\n\n"
            "اضغط الزرّ لفحص فوري 👇"
        )
        await message.answer(text, reply_markup=start_keyboard())

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        lines = ["📘 <b>أوامر بوت الحارس</b>", ""]
        lines += [f"/{name} — {desc}" for name, desc in BOT_COMMANDS]
        await message.answer("\n".join(lines))

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        await _send(message, await status_report(cfg))

    @router.message(Command("checks"))
    async def cmd_checks(message: Message) -> None:
        await _send(message, await checks_report(cfg))

    @router.message(Command("fix"))
    async def cmd_fix(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if not raw:
            names = "، ".join(ALL_CHECK_NAMES)
            await message.answer(
                "ℹ️ استعمل: <code>/fix &lt;اسم الفحص&gt;</code>\n"
                f"الأسماء المتاحة: {esc(names)}"
            )
            return
        outcome = await run_fixer(raw, cfg)
        if not outcome:
            name = esc(raw)
            await message.answer(
                f"⚠️ لا أعرف الفحص «{name}». استعمل /checks لرؤية الأسماء، "
                "أو أرسل اسم مُصلح صريح من FIXERS."
            )
            return
        await message.answer(outcome)

    @router.message(Command("restart"))
    async def cmd_restart(message: Message) -> None:
        outcome = await run_fixer("full_restart", cfg)
        await message.answer(outcome or "⚠️ مُصلح إعادة التشغيل غير متاح.")

    @router.message(Command("logs"))
    async def cmd_logs(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        count = DEFAULT_LOG_LINES
        if raw:
            try:
                count = int(raw)
            except ValueError:
                count = DEFAULT_LOG_LINES
        excerpt = await asyncio.to_thread(read_log_excerpt, cfg, count)
        for part in paginate(excerpt, limit=TELEGRAM_LIMIT - 32):
            await message.answer(f"<code>{esc(part)}</code>")

    @router.message(Command("incidents"))
    async def cmd_incidents(message: Message) -> None:
        items = await asyncio.to_thread(recent_incidents, cfg, 10)
        await _send(message, format_incidents(items))

    @router.message(Command("mute"))
    async def cmd_mute(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if not raw or not raw.isdigit():
            await message.answer("ℹ️ استعمل: <code>/mute &lt;دقائق&gt;</code> — مثال: <code>/mute 60</code>")
            return
        until = set_mute(cfg, int(raw))
        minutes = max(1, int(raw))
        when = time.strftime("%H:%M", time.localtime(until))
        await message.answer(
            f"🔇 كُتمت تنبيهات بوت الحارس {minutes} دقيقة (تنتهي في {when}).\n"
            "أوامرك تبقى تعمل كما هي."
        )

    @router.message(Command("unmute"))
    async def cmd_unmute(message: Message) -> None:
        clear_mute(cfg)
        await message.answer("🔔 أُلغي الكتم — تنبيهات الحارس تعمل من جديد.")

    @router.callback_query(lambda query: query.data == CHECK_CALLBACK)
    async def on_check_button(query: CallbackQuery) -> None:
        await query.answer("⏳ أفحص الآن…")
        await _send(query, await status_report(cfg))

    return router


def create_dispatcher(cfg: GuardianConfig) -> Dispatcher:
    """يبني المُوزِّع بوسطاء المشروع: حاجز الأخطاء ثم تحقّق المشرف."""
    admins = cfg.admin_chat_ids
    dp = Dispatcher()

    dp.message.outer_middleware(ErrorMiddleware())
    dp.callback_query.outer_middleware(ErrorMiddleware())
    dp.message.outer_middleware(GuardianAccessMiddleware(admins))
    dp.callback_query.outer_middleware(GuardianAccessMiddleware(admins))

    dp.include_router(build_router(cfg))
    return dp


async def set_profile(bot: Bot) -> None:
    """يضبط أوامر البوت ووصفه — مطابق لهوية «حارس نور الإسلام»."""
    commands = [BotCommand(command=name, description=desc) for name, desc in BOT_COMMANDS]
    for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()):
        try:
            await bot.set_my_commands(commands, scope=scope)
        except Exception as exc:  # pragma: no cover
            log.warning("set_my_commands فشل: %s", exc)
    try:
        await bot.set_my_description(description=DESCRIPTION)
        await bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
    except Exception as exc:  # pragma: no cover
        log.warning("set_my_description فشل: %s", exc)


async def _run(cfg: GuardianConfig) -> None:
    bot = Bot(token=cfg.guardian_bot_token, parse_mode=ParseMode.HTML)
    dp = create_dispatcher(cfg)
    dp.startup.register(set_profile)

    me = await bot.get_me()
    log.info("🛡️ بوت الحارس يعمل: @%s — مشرفون: %s", me.username, list(cfg.admin_chat_ids))
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


MISSING_TOKEN_MESSAGE = """\
🛡️ بوت الحارس التفاعلي غير مُفعّل: لا يوجد توكن.

لتفعيله (خطوة يدوية واحدة من المالك):
1) افتح تيليجرام وابحث عن @BotFather.
2) أرسل /newbot واتّبع الخطوات حتى تحصل على توكن.
3) ضع التوكن في متغيّر البيئة: GUARDIAN_BOT_TOKEN=...
4) شغّل: bash scripts/run_guardian_bot.sh

ملاحظة: الحارس يعمل بلا هذه القناة؛ التنبيه يصل عبر تقويم Google والبوت الهدف.
سيخرج الآن برمز 2 (لا خطأ)."""


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    """نقطة الدخول. تُعيد 2 إن غاب توكن بوت الحارس (بلا traceback)."""
    _setup_logging()
    token = (config.guardian_bot_token or "").strip()
    if not token:
        print(MISSING_TOKEN_MESSAGE)
        return 2
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        print("\nتوقّف بوت الحارس.")
        return 0
    except Exception as exc:  # noqa: BLE001 - لا traceback للمستخدم
        print(f"🛑 تعذّر تشغيل بوت الحارس: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
