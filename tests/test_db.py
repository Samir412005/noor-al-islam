"""اختبارات قاعدة البيانات (SQLite في الذاكرة)."""
from __future__ import annotations

import pytest

from app.config import settings
from app.db import Database


@pytest.fixture
async def database():
    db = Database(":memory:")
    await db.connect()
    yield db
    await db.close()


async def test_new_user_gets_defaults(database: Database) -> None:
    user = await database.get_user(101, "سمير")
    assert user.chat_id == 101
    assert user.first_name == "سمير"
    assert user.city == settings.default_city
    assert user.method == settings.default_method
    assert user.reciter == settings.default_reciter
    assert user.daily_push == settings.daily_push


async def test_user_is_created_once(database: Database) -> None:
    await database.get_user(102, "أ")
    await database.get_user(102, "ب")
    stats = await database.stats()
    assert stats["users"] == 1


async def test_update_user_only_allowed_fields(database: Database) -> None:
    await database.get_user(103)
    await database.update_user(103, city="وهران", method=19, daily_push=True, evil="DROP TABLE")
    user = await database.get_user(103)
    assert user.city == "وهران"
    assert user.method == 19
    assert user.daily_push is True


async def test_prefs_toggle(database: Database) -> None:
    await database.get_user(104)
    await database.update_pref(104, "push_quran", False)
    user = await database.get_user(104)
    assert user.push_quran is False
    assert user.push_adhkar is True


async def test_counters_accumulate(database: Database) -> None:
    assert await database.get_counter(105, "tasbih") == 0
    assert await database.bump_counter(105, "tasbih") == 1
    assert await database.bump_counter(105, "tasbih", 4) == 5


async def test_history_is_trimmed(database: Database) -> None:
    for index in range(20):
        await database.add_history(106, "user", f"سؤال {index}", keep=6)
    history = await database.get_history(106, limit=10)
    assert len(history) == 6
    assert history[-1]["content"] == "سؤال 19"  # الأحدث آخراً


async def test_clear_history(database: Database) -> None:
    await database.add_history(107, "user", "سؤال")
    await database.clear_history(107)
    assert await database.get_history(107) == []


async def test_push_state_is_recorded_after_success(database: Database) -> None:
    """لا يُسجَّل الإرسال إلا صراحةً بعد النجاح — حتى لا يضيع إشعار اليوم."""
    assert await database.push_already_sent(108, "quran", "2026-10-06") is False
    await database.mark_push_sent(108, "quran", "2026-10-06")
    assert await database.push_already_sent(108, "quran", "2026-10-06") is True
    assert await database.push_already_sent(108, "quran", "2026-10-07") is False
    # التسجيل المتكرّر لا يرفع استثناء (INSERT OR IGNORE)
    await database.mark_push_sent(108, "quran", "2026-10-06")


async def test_subscribers_respect_flags(database: Database) -> None:
    await database.get_user(109)
    await database.get_user(110)
    await database.update_user(109, daily_push=True)
    await database.update_pref(109, "push_quran", False)

    all_subs = await database.subscribers()
    assert [u.chat_id for u in all_subs] == [109]

    quran_subs = await database.subscribers("push_quran")
    assert quran_subs == []

    assert len(await database.subscribers("push_adhkar")) == 1
