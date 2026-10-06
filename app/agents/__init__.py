"""سجلّ الوكلاء — نقطة واحدة لتسجيلهم والعثور عليهم.

ترتيب `AGENT_ORDER` مهم: يُستعمل عند تعادل الدرجات (الأخصّ أولاً).
"""
from __future__ import annotations

from .adhkar import AdhkarAgent
from .base import Agent, BaseAgent
from .hadith import HadithAgent
from .occasions import OccasionsAgent
from .prayer import PrayerAgent
from .quran import QuranAgent
from .scholar import ScholarAgent

# الوكيل الافتراضي عند غياب نيّة واضحة
DEFAULT_AGENT = "scholar"

AGENT_ORDER: tuple[str, ...] = (
    "prayer",
    "quran",
    "hadith",
    "adhkar",
    "occasions",
    "scholar",
)

_AGENTS: dict[str, BaseAgent] = {
    agent.name: agent
    for agent in (
        PrayerAgent(),
        QuranAgent(),
        HadithAgent(),
        AdhkarAgent(),
        OccasionsAgent(),
        ScholarAgent(),
    )
}


def register(agent: BaseAgent) -> BaseAgent:
    """يسجّل وكيلاً جديداً (يمكّن الإضافة بلا تعديل الموجّه)."""
    _AGENTS[agent.name] = agent
    if agent.name not in AGENT_ORDER:
        globals()["AGENT_ORDER"] = AGENT_ORDER + (agent.name,)
    return agent


def get_agent(name: str) -> BaseAgent | None:
    return _AGENTS.get((name or "").strip().lower())


def all_agents() -> list[BaseAgent]:
    ordered = [a for name in AGENT_ORDER if (a := _AGENTS.get(name))]
    extras = [a for name, a in _AGENTS.items() if a not in ordered]
    return ordered + extras


def agent_names() -> list[str]:
    return [a.name for a in all_agents()]


__all__ = [
    "Agent",
    "BaseAgent",
    "DEFAULT_AGENT",
    "AGENT_ORDER",
    "register",
    "get_agent",
    "all_agents",
    "agent_names",
]
