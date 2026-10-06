"""اختبارات الإشعارات المجدولة: النافذة الزمنية ومنع التكرار وعدم الإزعاج."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import scheduler
from app.db import Database
from app.models import AgentReply


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture
async def database(monkeypatch: pytest.MonkeyPatch):
    test_db = Database(":memory:")
    await test_db.connect()
    monkeypatch.setattr(scheduler, "db", test_db)
    monkeypatch.setattr(
        scheduler,
        "_payload",
        lambda kind, user: _payload(kind, user),
    )
    yield test_db
    await test_db.close()


async def _payload(kind, user) -> AgentReply:
    return AgentReply(text=f"محتوى {kind}", agent="test")


async def _subscribe(database: Database, chat_id: int, tz: str = "Africa/Algiers") -> None:
    await database.get_user(chat_id, "سمير")
    await database.update_user(chat_id, daily_push=True, tz=tz)


def _at(hour: int, minute: int, tz: str = "Africa/Algiers") -> datetime:
    return datetime(2026, 10, 6, hour, minute, tzinfo=ZoneInfo(tz))


async def test_nothing_is_sent_before_its_time(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _subscribe(database, 1)
    monkeypatch.setattr(scheduler, "_now", lambda tz: _at(4, 0))

    bot = FakeBot()
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert bot.sent == []


async def test_scheduled_push_is_sent_once(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _subscribe(database, 2)
    monkeypatch.setattr(scheduler, "_now", lambda tz: _at(6, 35))

    bot = FakeBot()
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert len(bot.sent) == 1
    assert "adhkar_morning" in bot.sent[0][1]

    # دورة ثانية في نفس اليوم ⇒ لا تكرار
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert len(bot.sent) == 1


async def test_startup_burst_is_prevented(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """إقلاع متأخر (٢٠:٠٠) لا يجب أن يُفرغ أربعة إشعارات دفعة واحدة."""
    await _subscribe(database, 3)
    monkeypatch.setattr(scheduler, "_now", lambda tz: _at(20, 0))

    bot = FakeBot()
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert bot.sent == []

    # وطُويت بصمت ⇒ لن تُرسل لاحقاً في اليوم نفسه
    for kind, _, _, _ in scheduler.SCHEDULE:
        assert await database.push_already_sent(3, kind, "2026-10-06")


async def test_window_keeps_recent_push(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """داخل النافذة (٤٥ دقيقة) يُرسل؛ بعدها يُطوى."""
    await _subscribe(database, 4)
    monkeypatch.setattr(scheduler, "_now", lambda tz: _at(9, 0))  # ٦٠ دقيقة بعد ٨:٠٠
    bot = FakeBot()
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert bot.sent == []

    await _subscribe(database, 5)
    monkeypatch.setattr(scheduler, "_now", lambda tz: _at(8, 30))  # ٣٠ دقيقة
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert [chat for chat, _ in bot.sent] == [5]


async def test_failed_payload_is_not_marked_sent(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _subscribe(database, 6)
    monkeypatch.setattr(scheduler, "_now", lambda tz: _at(8, 10))
    monkeypatch.setattr(scheduler, "_payload", lambda kind, user: _none())

    bot = FakeBot()
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert bot.sent == []
    assert await database.push_already_sent(6, "quran", "2026-10-06") is False


async def _none():
    return None


async def test_disabled_preferences_are_respected(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _subscribe(database, 7)
    await database.update_pref(7, "push_adhkar", False)
    monkeypatch.setattr(scheduler, "_now", lambda tz: _at(6, 35))

    bot = FakeBot()
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert bot.sent == []


async def test_user_timezone_is_used(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """٦:٣٥ في طوكيو ليست ٦:٣٥ في الجزائر."""
    await _subscribe(database, 8, tz="Asia/Tokyo")
    seen: list[str] = []

    def fake_now(tz: str) -> datetime:
        seen.append(tz)
        return datetime(2026, 10, 6, 6, 35, tzinfo=ZoneInfo(tz))

    monkeypatch.setattr(scheduler, "_now", fake_now)
    bot = FakeBot()
    await scheduler._send_daily(bot)  # type: ignore[arg-type]
    assert seen == ["Asia/Tokyo"]
    assert len(bot.sent) == 1
