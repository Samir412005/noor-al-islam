"""الموجّه (Router): من رسالة المستخدم إلى الوكيل المناسب.

الترتيب المتّبع — من الأرخص والأدقّ إلى الأغلى:
1. تعليمة صريحة (/quran, /hadith …) أو ضغطة زر (ag:<agent>:…).
2. مطابقة كلمات مفتاحية (بلا شبكة، فورية).
3. تصنيف بالـLLM عند الغموض (اختياري، إن كان مفعّلاً).
4. الوكيل الافتراضي (وكيل العلم) الذي يبحث في كل المصادر.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .agents import DEFAULT_AGENT, AGENT_ORDER, all_agents, get_agent
from .agents.base import extract_arabic_ayah_reference
from .llm import LLMError, llm
from .models import AgentRequest

log = logging.getLogger(__name__)

COMMANDS: dict[str, str] = {
    "quran": "quran",
    "quran_ar": "quran",
    "قرآن": "quran",
    "hadith": "hadith",
    "حديث": "hadith",
    "adhkar": "adhkar",
    "اذكار": "adhkar",
    "أذكار": "adhkar",
    "prayer": "prayer",
    "مواقيت": "prayer",
    "occasions": "occasions",
    "مناسبات": "occasions",
    "ask": "scholar",
    "scholar": "scholar",
    "اسأل": "scholar",
}

# أقلّ درجة تُقبل من المطابقة بالكلمات المفتاحية
KEYWORD_THRESHOLD = 1.0


@dataclass
class Route:
    agent: str
    args: str = ""
    reason: str = "keyword"
    score: float = 0.0


def _command_name(raw: str) -> str:
    cleaned = (raw or "").strip().lstrip("/").split("@")[0].strip()
    return cleaned.split()[0].lower() if cleaned else ""


def explicit_route(raw_command: str) -> Route | None:
    """تعليمة صريحة أو زر."""
    raw = (raw_command or "").strip()

    if raw.startswith("ag:"):
        parts = raw.split(":")
        if len(parts) >= 3:
            name = parts[1].strip().lower()
            if get_agent(name):
                # args = بقيّة النداء بلا بادئة (timings / get:bukhari:1 / item:12:3)
                return Route(agent=name, args=":".join(parts[2:]), reason="callback")

    name = _command_name(raw)
    if name in COMMANDS:
        return Route(agent=COMMANDS[name], args="", reason="command")
    return None


def ayah_reference_route(text: str) -> Route | None:
    """«2:255» أو «البقرة 255» أو «آية الكرسي» ⇒ وكيل القرآن.

    يُشترط قِصر النصّ حتى لا يُفسَّر رقمٌ عابر داخل جملة كمرجع آية.
    """
    candidate = (text or "").strip()
    if not candidate or len(candidate) > 30:
        return None
    # لا تخطف أرقام الأحاديث («البخاري ١» ليست مرجع آية)
    from .services import hadith_api

    if hadith_api.find_collection(candidate) is not None:
        return None
    if "الكرسي" in candidate:
        return Route(agent="quran", reason="ayah_ref")
    if extract_arabic_ayah_reference(candidate) is not None:
        return Route(agent="quran", reason="ayah_ref")
    return None


def keyword_route(text: str) -> Route | None:
    """أعلى درجة مطابقة بين الوكلاء."""
    if not text or len(text.strip()) < 2:
        return None
    scored = [(agent.score(text), agent.name) for agent in all_agents()]
    scored = [item for item in scored if item[0] >= KEYWORD_THRESHOLD]
    if not scored:
        return None
    order = {name: index for index, name in enumerate(AGENT_ORDER)}
    scored.sort(key=lambda item: (-item[0], order.get(item[1], 99)))
    score, name = scored[0]
    return Route(agent=name, reason="keyword", score=score)


CLASSIFY_SYSTEM = """أنت مصنّف نوايا لبوت إسلامي. مهمّتك اختيار الوكيل الأنسب لرسالة المستخدم.

الوكلاء:
- quran: القرآن، الآيات، السور، التفسير، التلاوة، البحث في نصّ القرآن.
- hadith: الأحاديث النبوية، تخريجها، البحث في كتب الحديث.
- adhkar: الأذكار والأدعية وحصن المسلم وعدّاد التسبيح.
- prayer: مواقيت الصلاة، الأذان، القبلة، التاريخ الهجري.
- occasions: المناسبات الإسلامية (رمضان، العيدان، عرفة، عاشوراء) وتواريخها.
- scholar: أسئلة معرفية أو عامة، سؤال عن معنى/شرح، أو أي شيء لا يخصّ ما سبق.

أعِد JSON فقط بهذا الشكل دون أي شرح: {"agent": "<الاسم>", "args": "<نص قصير اختياري>"}
وإن لم تكن واثقاً أعِد {"agent": "scholar", "args": ""}."""


async def llm_route(text: str) -> Route | None:
    """تصنيف بالـLLM — يُستدعى فقط عند غياب إشارة واضحة."""
    if not llm.enabled or len(text.strip()) < 3:
        return None
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM},
        {"role": "user", "content": text[:500]},
    ]
    try:
        raw = await llm.chat(messages, max_tokens=60, temperature=0.0)
    except LLMError as exc:  # pragma: no cover - يعتمد على الشبكة
        log.warning("llm routing failed: %s", exc)
        return None
    name, args = _parse_classification(raw)
    if name and get_agent(name):
        return Route(agent=name, args=args, reason="llm")
    return None


def _parse_classification(raw: str) -> tuple[str, str]:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
    try:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        data = json.loads(cleaned[start : end + 1]) if start != -1 else {}
    except (ValueError, TypeError):
        data = {}
    name = str(data.get("agent", "")).strip().lower()
    args = str(data.get("args", "") or "").strip()
    aliases = {"quran": "quran", "hadith": "hadith", "adhkar": "adhkar",
               "prayer": "prayer", "occasions": "occasions", "scholar": "scholar"}
    return aliases.get(name, ""), args


async def route(request: AgentRequest, *, use_llm: bool = True) -> Route:
    """يحدّد الوكيل والوسائط للطلب."""
    explicit = explicit_route(request.raw_command or "")
    if explicit:
        return explicit

    reference = ayah_reference_route(request.text or "")
    if reference:
        return reference

    keyword = keyword_route(request.text or "")
    if keyword:
        return keyword

    if use_llm:
        smart = await llm_route(request.text or "")
        if smart:
            return smart

    return Route(agent=DEFAULT_AGENT, reason="default")


async def fallback_route(request: AgentRequest) -> Route:
    """مسار التعافي: نجرّب كل الوكلاء بالكلمات المفتاحية ثم نستسلم لوكيل العلم."""
    for candidate in (request.text or "", request.args or ""):
        found = keyword_route(candidate)
        if found:
            return found
    return Route(agent=DEFAULT_AGENT, reason="fallback")
