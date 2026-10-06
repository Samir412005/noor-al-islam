"""اختبارات طبقة المعالجة (core) وترجمة أزرار الوكلاء."""
from __future__ import annotations

import pytest

from app.agents import get_agent
from app.handlers import callbacks as cb
from app.handlers.core import DEFAULT_PROMPTS, build_request, process_text
from app.models import AgentReply, AgentRequest, UserProfile

USER = UserProfile(chat_id=7, first_name="سمير")


async def test_fatwa_blocked_before_any_agent() -> None:
    reply = await process_text("ما حكم بيع الذهب بالتقسيط؟", 7, USER)
    assert reply.agent == "guardrails"
    assert "الفتوى" in reply.text


async def test_crisis_blocked_before_any_agent() -> None:
    reply = await process_text("أريد أن أنهي حياتي", 7, USER)
    assert reply.agent == "guardrails"
    assert reply.meta.get("guard") == "self_harm"


async def test_every_agent_has_a_default_prompt_and_handles_it() -> None:
    """كل وكيل معه نصّ افتراضي عند أمر بلا وسائط."""
    for name, prompt in DEFAULT_PROMPTS.items():
        assert name in {"quran", "hadith", "adhkar", "prayer", "occasions", "scholar"}
        agent = get_agent(name)
        assert agent is not None
        # لا نتحقّق من الشبكة هنا: نتحقّق فقط من إمكانية التوجيه
        request = build_request(prompt, 7, USER, raw_command=f"/{name}")
        assert request.text == prompt


async def test_process_uses_agent_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """يعمل الاختبار بوكيل وهمي مسجَّل — دليل على قابلية التوسّع."""
    from app.agents import register
    from app.agents.base import BaseAgent

    class EchoAgent(BaseAgent):
        name = "echo"
        title = "وكيل الاختبار"
        keywords = ["صدى"]
        priority = 99

        async def handle(self, request: AgentRequest) -> AgentReply:
            return self.reply(f"صدى: {request.text}", agent=self.name)

    register(EchoAgent())
    reply = await process_text("صدى مرحبا", 7, USER)
    assert reply.agent == "echo" and "صدى: صدى مرحبا" == reply.text


@pytest.mark.parametrize(
    ("data", "expected_text"),
    [
        ("ag:quran:random", "آية اليوم"),
        ("ag:quran:tafsir:2:255", "تفسير 2:255"),
        ("ag:quran:tafsirkind:ar.qurtubi:2:255", "تفسير 2:255 ar.qurtubi"),
        ("ag:quran:audio:1:1", "تلاوة 1:1"),
        ("ag:quran:surah:67", "سورة 67"),
        ("ag:quran:search:الصبر", "ابحث عن الصبر"),
        ("ag:quran:audiosurah:67", None),
    ],
)
def test_quran_callback_translation(data: str, expected_text: str | None) -> None:
    assert cb.quran_text_from(":".join(data.split(":")[2:])) == expected_text


def test_build_request_conventions() -> None:
    """لكل وكيل عُرف وسائط — نتحقّق منه صراحة حتى لا ينكسر عند التعديل."""
    quran = cb.build_request("ag:quran:tafsir:2:255", 7, USER)
    assert quran is not None and quran.text == "تفسير 2:255" and quran.args == ""

    hadith = cb.build_request("ag:hadith:get:bukhari:1", 7, USER)
    assert hadith is not None and hadith.args == "ag:hadith:get:bukhari:1"

    adhkar = cb.build_request("ag:adhkar:item:12:3", 7, USER)
    assert adhkar is not None and adhkar.args == "item:12:3"

    prayer = cb.build_request("ag:prayer:qibla", 7, USER)
    assert prayer is not None and prayer.args == "qibla"

    assert cb.build_request("menu:main", 7, USER) is None
    assert cb.build_request("ag:", 7, USER) is None


def test_special_reply_for_full_surah_audio() -> None:
    reply = cb.special_reply("ag:quran:audiosurah:67", USER)
    assert reply is not None
    assert reply.audio_url and reply.audio_url.endswith(".mp3")
    assert "الملك" in reply.text

    assert cb.special_reply("ag:quran:tafsir:2:255", USER) is None
