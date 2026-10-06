"""الأوامر (/start, /quran, /city …)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ..agents import all_agents
from ..config import settings
from ..db import db
from ..keyboards import AGENT_LABELS, examples_menu, keyboard, main_menu
from ..models import Button, UserProfile
from ..services import prayer_api
from ..text import esc
from .core import DEFAULT_PROMPTS, process_text
from .render import send_reply

log = logging.getLogger(__name__)
router = Router(name="commands")

WELCOME = (
    "<b>🕌 نور الإسلام</b>\n"
    "رفيقك اليومي: قرآن، حديث، أذكار، مواقيت، ومناسبات — <b>بمصادرها</b>.\n\n"
    "ما يميّزني أنني <b>لا أُفتي ولا أختلق نصّاً</b>: كل آية وحديث أعرضه بمرجعه.\n\n"
    "اختر من القائمة، أو اكتب سؤالك مباشرة.\n"
    "<i>مثال: «أذكار الصباح» · «البقرة ٢٥٥» · «أحاديث عن النية» · «مواقيت الصلاة»</i>"
)

HELP = (
    "<b>📘 كيف تستعملني</b>\n\n"
    "<b>الأوامر</b>\n"
    "• /quran — وكيل القرآن (آية اليوم، تفسير، تلاوة، بحث)\n"
    "• /hadith — وكيل الحديث (حديث اليوم، تخريج، بحث)\n"
    "• /adhkar — وكيل الأذكار (أذكار الصباح والمساء والنوم، سبحة)\n"
    "• /prayer — مواقيت الصلاة والقبلة والتاريخ الهجري\n"
    "• /occasions — المناسبات الإسلامية القادمة\n"
    "• /ask — اسأل وكيل العلم\n"
    "• /city <المدينة> — تحديد مدينتك\n"
    "• /settings — الإعدادات · /menu — القائمة\n\n"
    "<b>أمثلة حرّة</b>\n"
    "• «2:255» أو «آية الكرسي» — آية بتفسيرها\n"
    "• «تلاوة الفاتحة» — صوت\n"
    "• «البخاري 1» أو «أحاديث عن بر الوالدين»\n"
    "• «أذكار النوم» · «دعاء الهم والحزن»\n"
    "• «مواقيت الصلاة في وهران» · «كم بقي للفجر»\n\n"
    "<i>لا أُفتي؛ وللحكم الشرعي اسأل أهل العلم.</i>"
)


async def _show_root(message: Message, user: UserProfile) -> None:
    await message.answer(WELCOME, reply_markup=main_menu(), disable_web_page_preview=True)


@router.message(CommandStart())
async def cmd_start(message: Message, user: UserProfile) -> None:
    await _show_root(message, user)


@router.message(Command("menu", "قائمة"))
async def cmd_menu(message: Message, user: UserProfile) -> None:
    await _show_root(message, user)


@router.message(Command("help", "مساعدة"))
async def cmd_help(message: Message, user: UserProfile) -> None:
    await message.answer(HELP, reply_markup=keyboard([[Button("⬅️ الرئيسية", "menu:main")]]), disable_web_page_preview=True)


@router.message(Command("about", "عن"))
async def cmd_about(message: Message, user: UserProfile) -> None:
    text = (
        "<b>ℹ️ عن البوت</b>\n\n"
        "بوت <b>نور الإسلام</b> — ستّة وكلاء يعملون معاً:\n"
        + "\n".join(f"• {AGENT_LABELS.get(a.name, a.name)} — {esc(a.title)}" for a in all_agents())
        + "\n\n<b>المصادر</b>\n"
        "• القرآن والتفسير: api.alquran.cloud\n"
        "• الحديث: fawazahmed0/hadith-api\n"
        "• الأذكار: حصن المسلم (القحطاني)\n"
        "• المواقيت: AlAdhan API\n\n"
        "<b>سياسة المحتوى</b>\n"
        "البوت ناقلٌ للنصوص لا مُفتٍ، ولا يعرض نصّاً بلا مرجع. "
        "والمحتوى بحاجة إلى <b>مراجعة من أهل العلم</b> قبل الاستعمال العام.\n\n"
        f"<i>النسخة 2.0 — المحرّك الحواري: {'مفعّل 🧠' if settings.llm_enabled else 'غير مفعّل (بحث مباشر)'}</i>"
    )
    await message.answer(text, reply_markup=keyboard([[Button("⬅️ الرئيسية", "menu:main")]]), disable_web_page_preview=True)


@router.message(Command("id", "معرفي"))
async def cmd_id(message: Message, user: UserProfile) -> None:
    from ..text import to_arabic_digits

    await message.answer(
        f"معرّفك: <code>{message.from_user.id}</code>\n"
        f"مدينتك: <b>{esc(user.city)}</b> · الطريقة: <code>{to_arabic_digits(user.method)}</code>",
        reply_markup=keyboard([[Button("⚙️ الإعدادات", "menu:settings")]]),
    )


@router.message(Command("city", "مدينة"))
async def cmd_city(message: Message, user: UserProfile, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "اكتب اسم مدينتك بعد الأمر، مثل:\n<code>/city وهران</code>\n"
            f"<i>مدينتك الحالية: {esc(user.city)}</i>",
        )
        return
    found = await prayer_api.find_city_coords(query)
    if not found:
        await message.answer(f"لم أجد مدينة باسم «{esc(query)}». جرّب اسماً أقرب، مثال: /city Constantine")
        return
    await db.update_user(
        user.chat_id,
        city=found.get("name") or query,
        country=found.get("country") or user.country,
        lat=found["latitude"],
        lon=found["longitude"],
        tz=found.get("timezone") or user.tz,
    )
    await message.answer(
        f"✅ تمّ ضبط مدينتك على <b>{esc(found.get('name') or query)}</b> "
        f"({esc(found.get('country') or '')}) — سأحسب مواقيتك عليها.",
        reply_markup=keyboard([[Button("🕌 المواقيت الآن", "ag:prayer:timings"), Button("📍 القبلة", "ag:prayer:qibla")]]),
    )


@router.message(Command("settings", "إعدادات"))
async def cmd_settings(message: Message, user: UserProfile) -> None:
    from ..keyboards import settings_menu
    from ..services import quran_api

    reciter = quran_api.RECITERS.get(user.reciter, user.reciter)
    method = prayer_api.METHODS.get(user.method, str(user.method))
    await message.answer(
        "<b>⚙️ الإعدادات</b>\n\n"
        f"📍 المدينة: <b>{esc(user.city)}</b>\n"
        f"🧮 طريقة الحساب: <b>{esc(method)}</b>\n"
        f"🎧 القارئ: <b>{esc(reciter)}</b>\n"
        f"🔔 الإشعارات اليومية: <b>{'مفعّلة' if user.daily_push else 'موقوفة'}</b>",
        reply_markup=settings_menu(user.city, user.method, user.reciter, user.daily_push),
    )


def _agent_command(agent_name: str):
    async def handler(message: Message, user: UserProfile, command: CommandObject) -> None:
        text = (command.args or "").strip() or DEFAULT_PROMPTS.get(agent_name, "")
        reply = await process_text(text, message.chat.id, user, raw_command=f"/{agent_name}")
        await send_reply(message, reply)

    return handler


for _name, _labels in {
    "quran": ("quran", "قرآن"),
    "hadith": ("hadith", "حديث"),
    "adhkar": ("adhkar", "أذكار", "اذكار"),
    "prayer": ("prayer", "مواقيت", "صلاة"),
    "occasions": ("occasions", "مناسبات"),
    "scholar": ("ask", "scholar", "اسأل", "سؤال"),
}.items():
    router.message(Command(*_labels))(_agent_command(_name))


@router.message(Command("agents", "وكلاء"))
async def cmd_agents(message: Message, user: UserProfile) -> None:
    rows = [[Button(AGENT_LABELS.get(a.name, a.name), f"menu:{a.name}")] for a in all_agents()]
    await message.answer("<b>الوكلاء المتاحون</b>", reply_markup=keyboard(rows))
