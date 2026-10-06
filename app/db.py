"""طبقة قاعدة البيانات (SQLite عبر aiosqlite).

تحفظ: تفضيلات المستخدم، ذاكرة محادثة وكيل العلم، والعدّادات.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .config import settings
from .models import UserProfile

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=8000;   -- عمليتان على نفس الملف: ننتظر بدل أن نُخطئ
PRAGMA synchronous=NORMAL;  -- أسرع مع WAL وأمن كافٍ
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    chat_id     INTEGER PRIMARY KEY,
    first_name  TEXT    NOT NULL DEFAULT '',
    city        TEXT    NOT NULL DEFAULT 'الجزائر',
    country     TEXT    NOT NULL DEFAULT 'Algeria',
    lat         REAL,
    lon         REAL,
    method      INTEGER NOT NULL DEFAULT 3,
    reciter     TEXT    NOT NULL DEFAULT 'ar.alafasy',
    tz          TEXT    NOT NULL DEFAULT 'Africa/Algiers',
    daily_push  INTEGER NOT NULL DEFAULT 0,
    prefs       TEXT    NOT NULL DEFAULT '{}',
    created_at  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
    chat_id INTEGER NOT NULL,
    key     TEXT    NOT NULL,
    value   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, key)
);

CREATE TABLE IF NOT EXISTS history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role    TEXT    NOT NULL,
    content TEXT    NOT NULL,
    ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id, id DESC);

CREATE TABLE IF NOT EXISTS push_state (
    chat_id INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    day     TEXT    NOT NULL,
    PRIMARY KEY (chat_id, kind, day)
);
"""


class Database:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or settings.db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ── دورة الحياة ────────────────────────────────────────────────
    async def connect(self) -> None:
        if self._db is not None:
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("قاعدة البيانات غير مهيّأة — استدعِ connect() أولاً")
        return self._db

    async def _execute(self, sql: str, params: tuple = ()) -> None:
        async with self._lock:
            await self.db.execute(sql, params)
            await self.db.commit()

    # ── المستخدمون ─────────────────────────────────────────────────
    async def get_user(self, chat_id: int, first_name: str = "") -> UserProfile:
        async with self._lock:
            cursor = await self.db.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
            row = await cursor.fetchone()
            await cursor.close()

            if row is None:
                now = int(time.time())
                prefs = {
                    "push_quran": True,
                    "push_adhkar": True,
                    "push_prayer": True,
                }
                await self.db.execute(
                    """INSERT INTO users
                       (chat_id, first_name, city, country, lat, lon, method, reciter, tz,
                        daily_push, prefs, created_at, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        chat_id,
                        first_name,
                        settings.default_city,
                        settings.default_country,
                        settings.default_lat,
                        settings.default_lon,
                        settings.default_method,
                        settings.default_reciter,
                        settings.default_tz,
                        1 if settings.daily_push else 0,
                        json.dumps(prefs),
                        now,
                        now,
                    ),
                )
                await self.db.commit()
                return UserProfile(chat_id=chat_id, first_name=first_name)

            await self.db.execute(
                "UPDATE users SET last_seen = ?, first_name = COALESCE(NULLIF(?, ''), first_name) WHERE chat_id = ?",
                (int(time.time()), first_name, chat_id),
            )
            await self.db.commit()

        prefs = json.loads(row["prefs"] or "{}")
        return UserProfile(
            chat_id=row["chat_id"],
            city=row["city"],
            country=row["country"],
            lat=row["lat"] if row["lat"] is not None else settings.default_lat,
            lon=row["lon"] if row["lon"] is not None else settings.default_lon,
            method=row["method"],
            reciter=row["reciter"],
            tz=row["tz"],
            daily_push=bool(row["daily_push"]),
            push_quran=bool(prefs.get("push_quran", True)),
            push_adhkar=bool(prefs.get("push_adhkar", True)),
            push_prayer=bool(prefs.get("push_prayer", True)),
            first_name=row["first_name"] or "",
        )

    _ALLOWED_FIELDS = {
        "city",
        "country",
        "lat",
        "lon",
        "method",
        "reciter",
        "tz",
        "daily_push",
        "first_name",
    }

    async def update_user(self, chat_id: int, **fields: Any) -> None:
        clean = {k: v for k, v in fields.items() if k in self._ALLOWED_FIELDS}
        if not clean:
            return
        for key in ("daily_push",):
            if key in clean:
                clean[key] = 1 if clean[key] else 0
        assignments = ", ".join(f"{k} = ?" for k in clean)
        params = tuple(clean.values()) + (chat_id,)
        await self._execute(f"UPDATE users SET {assignments} WHERE chat_id = ?", params)

    async def update_pref(self, chat_id: int, key: str, value: bool) -> None:
        user = await self.get_user(chat_id)
        prefs = {
            "push_quran": user.push_quran,
            "push_adhkar": user.push_adhkar,
            "push_prayer": user.push_prayer,
        }
        prefs[key] = bool(value)
        await self._execute(
            "UPDATE users SET prefs = ? WHERE chat_id = ?", (json.dumps(prefs), chat_id)
        )

    # ── العدّادات ─────────────────────────────────────────────────
    async def bump_counter(self, chat_id: int, key: str, delta: int = 1) -> int:
        await self._execute(
            """INSERT INTO counters (chat_id, key, value) VALUES (?,?,?)
               ON CONFLICT(chat_id, key) DO UPDATE SET value = value + excluded.value""",
            (chat_id, key, delta),
        )
        return await self.get_counter(chat_id, key)

    async def get_counter(self, chat_id: int, key: str) -> int:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT value FROM counters WHERE chat_id = ? AND key = ?", (chat_id, key)
            )
            row = await cursor.fetchone()
            await cursor.close()
        return int(row["value"]) if row else 0

    # ── ذاكرة الحوار ───────────────────────────────────────────────
    async def add_history(self, chat_id: int, role: str, content: str, keep: int = 12) -> None:
        await self._execute(
            "INSERT INTO history (chat_id, role, content, ts) VALUES (?,?,?,?)",
            (chat_id, role, content[:4000], int(time.time())),
        )
        await self._execute(
            """DELETE FROM history WHERE chat_id = ? AND id NOT IN
               (SELECT id FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT ?)""",
            (chat_id, chat_id, keep),
        )

    async def get_history(self, chat_id: int, limit: int = 8) -> list[dict[str, str]]:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT role, content FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (chat_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def clear_history(self, chat_id: int) -> None:
        await self._execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))

    # ── الإشعارات اليومية ─────────────────────────────────────────
    async def push_already_sent(self, chat_id: int, kind: str, day: str) -> bool:
        """قراءة فقط: هل أُرسل هذا الإشعار اليوم؟"""
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT 1 FROM push_state WHERE chat_id = ? AND kind = ? AND day = ?",
                (chat_id, kind, day),
            )
            exists = await cursor.fetchone()
            await cursor.close()
        return exists is not None

    async def mark_push_sent(self, chat_id: int, kind: str, day: str) -> None:
        """يُسجَّل **بعد** نجاح الإرسال فقط، حتى لا يضيع إشعار اليوم بفشل عابر."""
        await self._execute(
            "INSERT OR IGNORE INTO push_state (chat_id, kind, day) VALUES (?,?,?)",
            (chat_id, kind, day),
        )

    async def subscribers(self, kind: str | None = None) -> list[UserProfile]:
        sql = "SELECT chat_id FROM users WHERE daily_push = 1"
        async with self._lock:
            cursor = await self.db.execute(sql)
            rows = await cursor.fetchall()
            await cursor.close()
        users = []
        for row in rows:
            user = await self.get_user(row["chat_id"])
            if kind is None or getattr(user, kind, False):
                users.append(user)
        return users

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            cursor = await self.db.execute("SELECT COUNT(*) AS n FROM users")
            total = (await cursor.fetchone())["n"]
            cursor = await self.db.execute("SELECT COUNT(*) AS n FROM users WHERE daily_push = 1")
            subscribed = (await cursor.fetchone())["n"]
            cursor = await self.db.execute("SELECT COUNT(*) AS n FROM history")
            messages = (await cursor.fetchone())["n"]
            await cursor.close()
        return {"users": int(total), "subscribed": int(subscribed), "messages": int(messages)}


db = Database()
