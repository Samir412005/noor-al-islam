"""قلب طبقة المعالجات: حواجز ← توجيه ← وكيل ← ردّ (مشترك بين النصّ والأزرار)."""
from __future__ import annotations

import logging

from .. import guardrails
from ..agents import DEFAULT_AGENT, get_agent
from ..models import AgentReply, AgentRequest, UserProfile
from ..router import route

log = logging.getLogger(__name__)

# نصّ افتراضي يُنفَّذ عند أمرٍ بلا وسائط (‎/quran بلا سؤال…)
DEFAULT_PROMPTS: dict[str, str] = {
    "quran": "آية اليوم",
    "hadith": "حديث اليوم",
    "adhkar": "أذكار الصباح",
    "prayer": "مواقيت الصلاة",
    "occasions": "المناسبات القادمة",
    "scholar": "",
}


def build_request(
    text: str,
    chat_id: int,
    user: UserProfile,
    *,
    args: str = "",
    raw_command: str = "",
) -> AgentRequest:
    return AgentRequest(
        text=text or "",
        args=args or "",
        raw_command=raw_command or "",
        chat_id=chat_id,
        user=user,
    )


async def process(request: AgentRequest, *, use_llm: bool = True) -> AgentReply:
    """يشغّل مسار المعالجة الكامل ويعيد ردّاً جاهزاً للإرسال."""
    text = (request.text or "").strip()

    decision = guardrails.precheck(text)
    if decision.blocked and decision.response:
        return AgentReply(text=decision.response, agent="guardrails", meta={"guard": decision.category})

    selected = await route(request, use_llm=use_llm)
    if selected.args and not request.args:
        request.args = selected.args

    agent = get_agent(selected.agent) or get_agent(DEFAULT_AGENT)
    if agent is None:  # pragma: no cover - لا يحدث عملياً
        return AgentReply(text="⚠️ لا يوجد وكيل متاح لمعالجة طلبك الآن.", agent="none")

    try:
        reply = await agent.handle(request)
    except Exception:
        log.exception("agent %s crashed", agent.name)
        return AgentReply(
            text="⚠️ حدث خطأ غير متوقّع أثناء تنفيذ طلبك. أعِد المحاولة بعد قليل.",
            agent=agent.name,
        )

    if not reply.agent:
        reply.agent = agent.name
    if not reply.text and not reply.audio_url:
        reply.text = "لم أجد ما أعرضه. جرّب صياغة أخرى."
    return reply


async def process_text(
    text: str, chat_id: int, user: UserProfile, *, raw_command: str = "", use_llm: bool = True
) -> AgentReply:
    return await process(build_request(text, chat_id, user, raw_command=raw_command), use_llm=use_llm)
