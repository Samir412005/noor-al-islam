"""طبقة الوكلاء: عقد موحّد + سجلّ (registry).

كل وكيل يطبّق `Agent` ويعيد `AgentReply`. لا اعتماد على aiogram هنا إطلاقاً،
ما يسمح باختبار كل وكيل عبر `pytest` دون تشغيل بوت.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable, Protocol, runtime_checkable

from ..models import AgentReply, AgentRequest

Tool = Callable[..., Awaitable[str]]


@runtime_checkable
class Agent(Protocol):
    name: str
    title: str
    description: str
    examples: list[str]
    keywords: list[str]

    async def handle(self, request: AgentRequest) -> AgentReply:  # pragma: no cover
        ...


class BaseAgent:
    """أساس مشترك: مطابقة الكلمات المفتاحية ومساعدة إنشاء الردود."""

    name: str = "base"
    title: str = "وكيل"
    description: str = ""
    examples: list[str] = []
    keywords: list[str] = []
    priority: int = 50

    async def handle(self, request: AgentRequest) -> AgentReply:  # pragma: no cover
        raise NotImplementedError

    # ── تسجيل النية ───────────────────────────────────────────────
    def score(self, text: str) -> float:
        """درجة ملاءمة هذا الوكيل للنص (0 = لا يخصّه)."""
        if not text:
            return 0.0
        lowered = text.strip().lower()
        best = 0.0
        for keyword in self.keywords:
            kw = keyword.lower()
            if kw in lowered:
                # كلمة أطول ⇒ إشارة أقوى
                weight = 1.0 + min(len(kw), 12) / 20.0
                if lowered.startswith(kw):
                    weight += 0.4
                best = max(best, weight)
        return best

    # ── أدوات مساعدة ──────────────────────────────────────────────
    @staticmethod
    def reply(text: str, **kwargs) -> AgentReply:
        kwargs.setdefault("agent", "")
        return AgentReply(text=text, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r}>"


def extract_arabic_ayah_reference(text: str) -> tuple[int, int] | None:
    """يستخرج مرجع آية من نص مثل: «البقرة 255» أو «2:255»."""
    text = text.strip()
    normalized = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))

    colon = re.search(r"\b(\d{1,3})\s*[:/]\s*(\d{1,3})\b", normalized)
    if colon:
        return int(colon.group(1)), int(colon.group(2))

    # «سورة البقرة آية 255» / «البقرة 255»
    match = re.search(r"(\d{1,3})\s*[،,]?\s*(?:آية|ايه|اية)?\s*$", normalized)
    if match:
        number = int(match.group(1))
        if 1 <= number <= 286:
            return 0, number
    return None
