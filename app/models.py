"""نماذج البيانات المشتركة بين الوكلاء والخدمات.

الهدف: ألّا يعتمد منطق الوكلاء على `aiogram` إطلاقاً حتى يبقى قابلاً للاختبار
بشكل مستقل، بأسرع ما يمكن.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ParseMode = Literal["HTML", "MarkdownV2", "Markdown", "none"]


@dataclass(frozen=True)
class Button:
    """زر Inline مستقل عن مكتبة تيليجرام."""

    text: str
    data: str

    @property
    def callback_data(self) -> str:
        # حدّ تيليجرام 64 بايت
        return self.data.encode("utf-8")[:64].decode("utf-8", "ignore")


@dataclass
class UserProfile:
    """تفضيلات المستخدم — تُخزَّن في SQLite."""

    chat_id: int
    city: str = "الجزائر"
    country: str = "Algeria"
    lat: float = 36.7538
    lon: float = 3.0588
    method: int = 3
    reciter: str = "ar.alafasy"
    tz: str = "Africa/Algiers"
    daily_push: bool = False
    push_quran: bool = True
    push_adhkar: bool = True
    push_prayer: bool = True
    first_name: str = ""

    @property
    def display_name(self) -> str:
        return self.first_name.strip() or "أخي الكريم"


@dataclass
class AgentRequest:
    """طلب مُوجَّه إلى وكيل."""

    text: str
    chat_id: int
    user: UserProfile
    args: str = ""
    raw_command: str = ""
    locale: str = "ar"


@dataclass
class AgentReply:
    """ردّ وكيل — مستقل عن تيليجرام، يُحوَّل لاحقاً إلى رسائل."""

    text: str
    agent: str = ""
    buttons: list[list[Button]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    audio_url: str | None = None
    audio_title: str | None = None
    parse_mode: ParseMode = "HTML"
    disable_preview: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    def with_source(self, source: str) -> "AgentReply":
        if source and source not in self.sources:
            self.sources.append(source)
        return self


@dataclass(frozen=True)
class Source:
    """مرجع مُهيكل يُعرض في آخر الرد."""

    label: str

    def line(self) -> str:
        return f"المصدر: {self.label}"
