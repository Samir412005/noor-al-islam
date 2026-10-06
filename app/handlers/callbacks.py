"""تحويل ضغطات الأزرار (ag:<agent>:…) إلى طلبات وكلاء.

لكل وكيل عُرفٌ مختلف قليلاً في قراءة الوسائط (بقايا التكامل)، وهذا الملف هو
الموضع الوحيد الذي يعرف هذه الفروق، حتى تبقى الوكلاء أنفسهم مستقلّة ونقية.
"""
from __future__ import annotations

from ..agents.quran import QuranAgent
from ..models import AgentReply, AgentRequest, Button, UserProfile
from ..services import quran_api
from ..text import esc

AGENT_CALLBACK_PREFIX = "ag:"


def quran_text_from(rest: str) -> str | None:
    """يترجم نداءات أزرار وكيل القرآن إلى نصٍّ يفهمه الوكيل."""
    parts = [p for p in rest.split(":") if p != ""]
    if not parts:
        return None
    action = parts[0].lower()

    if action == "random":
        return "آية اليوم"
    if action == "tafsir" and len(parts) >= 3:
        return f"تفسير {parts[1]}:{parts[2]}"
    if action == "tafsirkind" and len(parts) >= 4:
        return f"تفسير {parts[2]}:{parts[3]} {parts[1]}"
    if action == "audio" and len(parts) >= 3:
        return f"تلاوة {parts[1]}:{parts[2]}"
    if action == "surah" and len(parts) >= 2:
        return f"سورة {parts[1]}"
    if action == "search" and len(parts) >= 2:
        return "ابحث عن " + ":".join(parts[1:])
    return None


def build_request(data: str, chat_id: int, user: UserProfile) -> AgentRequest | None:
    """يبني طلباً مناسباً من معرّف الزر."""
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "ag":
        return None
    agent = parts[1].strip().lower()
    rest = ":".join(parts[2:])

    if agent == "quran":
        text = quran_text_from(rest)
        if text is None:
            return None
        return AgentRequest(text=text, args="", raw_command=data, chat_id=chat_id, user=user)

    if agent == "hadith":
        # وكيل الحديث يقرأ النداء كاملاً من args
        return AgentRequest(text="", args=data, raw_command=data, chat_id=chat_id, user=user)

    return AgentRequest(text="", args=rest, raw_command=data, chat_id=chat_id, user=user)


def special_reply(data: str, user: UserProfile) -> AgentReply | None:
    """حالات خاصّة لا يغطّيها نصّ الوكيل (مثل تلاوة سورة كاملة)."""
    parts = [p for p in data.split(":") if p != ""]
    if len(parts) >= 3 and parts[0] == "ag" and parts[1] == "quran" and parts[2] == "audiosurah":
        target = parts[3] if len(parts) > 3 else ""
        surah = quran_api.find_surah(target)
        if surah is None:
            return AgentReply(text="لم أجد هذه السورة.", agent="quran")
        url = quran_api.surah_audio_url(surah["number"], user.reciter)
        reciter_name = quran_api.RECITERS.get(user.reciter, user.reciter)
        return AgentReply(
            text=(
                f"<b>🎧 سورة {esc(surah['name'])}</b>\n"
                f"<i>تلاوة كاملة — {esc(reciter_name)}</i>"
            ),
            agent="quran",
            audio_url=url,
            audio_title=f"سورة {surah['name']}",
            sources=[quran_api.SOURCE_QURAN],
            buttons=[[Button("📖 السورة", f"ag:quran:surah:{surah['number']}")]],
        )
    return None


__all__ = ["AGENT_CALLBACK_PREFIX", "build_request", "quran_text_from", "special_reply", "QuranAgent"]
