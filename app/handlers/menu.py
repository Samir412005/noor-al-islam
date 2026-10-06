"""قوائم الأزرار: menu:* و ex:* و set:*."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from ..agents import all_agents, get_agent
from ..db import db
from ..keyboards import AGENT_LABELS, back_menu, examples_menu, keyboard, main_menu, settings_menu
from ..models import Button, UserProfile
from ..services import prayer_api, quran_api
from ..text import esc
from .core import process, build_request
from .render import replace_reply, send_reply

log = logging.getLogger(__name__)
router = Router(name="menu")


@router.callback_query(F.data == "menu:main")
async def on_main(callback: CallbackQuery, user: UserProfile) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>🕌 القائمة الرئيسية</b>\nاختر وكيلاً أو اكتب سؤالك مباشرة.",
            reply_markup=main_menu(),
        )


@router.callback_query(F.data == "menu:about")
async def on_about(callback: CallbackQuery, user: UserProfile) -> None:
    from .commands import cmd_about

    await callback.answer()
    if callback.message:
        await cmd_about(callback.message, user)


@router.callback_query(F.data == "menu:help")
async def on_help(callback: CallbackQuery, user: UserProfile) -> None:
    from .commands import cmd_help

    await callback.answer()
    if callback.message:
        await cmd_help(callback.message, user)


@router.callback_query(F.data == "menu:settings")
async def on_settings(callback: CallbackQuery, user: UserProfile) -> None:
    from .commands import cmd_settings

    await callback.answer()
    if callback.message:
        await cmd_settings(callback.message, user)


@router.callback_query(F.data.startswith("ex:"))
async def on_example(callback: CallbackQuery, user: UserProfile) -> None:
    """زرّ مثال ⇒ يُنفَّذ كنصّ حقيقي."""
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    agent_name, index_raw = parts[1], parts[2]
    agent = get_agent(agent_name)
    await callback.answer()
    if agent is None or callback.message is None:
        return
    try:
        index = int(index_raw)
        text = agent.examples[index]
    except (ValueError, IndexError):
        text = (agent.examples or [""])[0]
    request = build_request(text, callback.message.chat.id, user, raw_command=f"/{agent_name}")
    reply = await process(request)
    await send_reply(callback.message, reply)


@router.callback_query(F.data == "set:push")
async def on_toggle_push(callback: CallbackQuery, user: UserProfile) -> None:
    new_value = not user.daily_push
    await db.update_user(user.chat_id, daily_push=new_value)
    await callback.answer("تم ✅" if new_value else "أُوقفت 🔕")
    if callback.message:
        user.daily_push = new_value
        method = prayer_api.METHODS.get(user.method, str(user.method))
        reciter = quran_api.RECITERS.get(user.reciter, user.reciter)
        await callback.message.edit_text(
            "<b>⚙️ الإعدادات</b>\n\n"
            f"📍 المدينة: <b>{esc(user.city)}</b>\n"
            f"🧮 طريقة الحساب: <b>{esc(method)}</b>\n"
            f"🎧 القارئ: <b>{esc(reciter)}</b>\n"
            f"🔔 الإشعارات اليومية: <b>{'مفعّلة' if new_value else 'موقوفة'}</b>",
            reply_markup=settings_menu(user.city, user.method, user.reciter, new_value),
        )


@router.callback_query(F.data == "menu:setcity")
async def on_setcity(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📍 اكتب اسم مدينتك بهذه الصيغة:\n<code>/city وهران</code>\n"
            "<i>أو اكتب مباشرة: «مواقيت الصلاة في قسنطينة» وسأحفظها.</i>",
        )


@router.callback_query(F.data == "menu:method")
async def on_method(callback: CallbackQuery, user: UserProfile) -> None:
    await callback.answer()
    if callback.message is None:
        return
    rows = [
        [Button(f"{name}", f"set:method:{key}")]
        for key, name in list(prayer_api.METHODS.items())[:8]
    ]
    rows.append([Button("⬅️ الإعدادات", "menu:settings")])
    await callback.message.answer("<b>🧮 اختر طريقة حساب المواقيت</b>", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("set:method:"))
async def on_set_method(callback: CallbackQuery, user: UserProfile) -> None:
    raw = (callback.data or "").split(":")[-1]
    try:
        method = int(raw)
    except ValueError:
        await callback.answer("قيمة غير صحيحة")
        return
    await db.update_user(user.chat_id, method=method)
    await callback.answer("تم الحفظ ✅")
    if callback.message:
        await callback.message.answer(
            f"✅ طريقة الحساب: <b>{esc(prayer_api.METHODS.get(method, str(method)))}</b>",
            reply_markup=keyboard([[Button("🕌 المواقيت الآن", "ag:prayer:timings"), Button("⬅️ الإعدادات", "menu:settings")]]),
        )


@router.callback_query(F.data == "menu:reciter")
async def on_reciter(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    rows = [[Button(name, f"set:reciter:{key}")] for key, name in quran_api.RECITERS.items()]
    rows.append([Button("⬅️ الإعدادات", "menu:settings")])
    await callback.message.answer("<b>🎧 اختر القارئ</b>", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("set:reciter:"))
async def on_set_reciter(callback: CallbackQuery, user: UserProfile) -> None:
    key = (callback.data or "").split(":", 2)[-1]
    if key not in quran_api.RECITERS:
        await callback.answer("قارئ غير معروف")
        return
    await db.update_user(user.chat_id, reciter=key)
    await callback.answer("تم الحفظ ✅")
    if callback.message:
        await callback.message.answer(
            f"✅ القارئ: <b>{esc(quran_api.RECITERS[key])}</b>\n"
            "جرّب الآن: <code>تلاوة الفاتحة</code>",
            reply_markup=keyboard([[Button("🎧 تلاوة الفاتحة", "ag:quran:audio:1:1"), Button("⬅️ الإعدادات", "menu:settings")]]),
        )


@router.callback_query(F.data.startswith("menu:"))
async def on_agent_menu(callback: CallbackQuery, user: UserProfile) -> None:
    """قائمة فرعية لكل وكيل مع أمثلته."""
    name = (callback.data or "").split(":", 1)[1]
    agent = get_agent(name)
    await callback.answer()
    if agent is None or callback.message is None:
        return
    label = AGENT_LABELS.get(agent.name, agent.name)
    body = (
        f"<b>{label} — {esc(agent.title)}</b>\n\n{esc(agent.description)}\n\n"
        "<b>أمثلة</b>\n" + "\n".join(f"• {esc(e)}" for e in agent.examples[:5])
    )
    await callback.message.answer(body, reply_markup=examples_menu(agent.name, agent.examples))
