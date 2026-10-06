"""وكيل الحديث — عرض الأحاديث بتخريجها من fawazahmed0/hadith-api.

قاعدة صارمة: لا يُختلق حديث، ولا يُحرَّف نصّ، ولا يُحذف الإسناد. إن لم يوجد
المصدر يُقال ذلك صراحة. كل النصوص الديناميكية تُمرَّر عبر `esc` لأن الرد HTML.
"""
from __future__ import annotations

import itertools
import re
from datetime import date

from ..models import AgentReply, AgentRequest, Button
from ..text import (
    chunk,
    clean,
    esc,
    normalize_arabic,
    to_arabic_digits,
)
from ..services import hadith_api
from .base import BaseAgent

SOURCE_LABEL = "fawazahmed0/hadith-api (النصوص العربية)"
DIVIDER = "━━━━━━━━━━━━"

_SEARCH_PREFIX = re.compile(
    r"^\s*(?:أ?بحث(?:\s+لي)?\s+عن|بحث\s+عن|أ?حاديث\s+عن|حديث\s+عن)\s+",
    re.UNICODE,
)
_HADITH_LEAD = re.compile(r"^\s*(?:حديث|أحاديث|احاديث)\s+", re.UNICODE)
_DAY_PATTERNS = ("حديث اليوم", "حديث يوم", "حديث هذا اليوم")


def _to_english_digits(text: str) -> str:
    return text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


# ── اختصار الاستعلامات الطويلة (حدّ تيليجرام: 64 بايت في data) ────────
class _Shortcuts:
    def __init__(self, limit: int = 256) -> None:
        self._items: dict[str, str] = {}
        self._counter = itertools.count(1)
        self._limit = limit

    def add(self, value: str) -> str:
        token = f"~{next(self._counter)}"
        self._items[token] = value
        while len(self._items) > self._limit:
            self._items.pop(next(iter(self._items)))
        return token

    def resolve(self, token: str) -> str:
        return self._items.get(token, token)


_shortcuts = _Shortcuts()


def _callback(action: str, *args: str) -> str:
    data = ":".join(("ag", "hadith", action, *args))
    if len(data.encode("utf-8")) <= 64:
        return data
    if action == "search" and args:
        # قصّ نصّ البحث ليبقى ضمن حدّ تيليجرام (64 بايت) بلا حالة في الذاكرة
        prefix = "ag:hadith:search:"
        room = 64 - len(prefix.encode("utf-8"))
        query = ":".join(args)
        truncated = query.encode("utf-8")[:room].decode("utf-8", "ignore")
        if truncated:
            return f"{prefix}{truncated}"
    # قصّ آمن لأي وسيط آخر طويل
    encoded = data.encode("utf-8")[:64]
    return encoded.decode("utf-8", "ignore")


class HadithAgent(BaseAgent):
    """وكيل الحديث: حديث اليوم، حديث برقم، بحث نصّي، وأبواب."""

    name = "hadith"
    title = "وكيل الحديث"
    description = "أحاديث الصحيحين والسنن والموطأ والأربعين النووية بتخريجها."
    priority = 85
    keywords = [
        "حديث",
        "أحاديث",
        "احاديث",
        "البخاري",
        "بخاري",
        "مسلم",
        "الترمذي",
        "النسائي",
        "ابن ماجه",
        "أبو داود",
        "النووية",
        "قدسي",
        "تخريج",
        "سند",
        "صحيح",
        "حديث اليوم",
        "ابحث عن حديث",
    ]
    examples = [
        "حديث اليوم",
        "حديث عن النية",
        "البخاري 1",
        "ابحث عن بر الوالدين",
        "النهي عن الغضب",
    ]

    # ── الواجهة ───────────────────────────────────────────────────
    async def handle(self, request: AgentRequest) -> AgentReply:
        try:
            return await self._dispatch(request)
        except Exception:
            return self._reply(
                "تعذّر الوصول إلى مصدر الحديث الآن. حاول بعد قليل، أو اكتب رقم "
                "الحديث مع اسم المصدر مثل: «البخاري 1»."
            )

    async def _dispatch(self, request: AgentRequest) -> AgentReply:
        raw = (request.args or "").strip()
        if raw.startswith("ag:hadith:") or request.raw_command in {"hadith", "ag:hadith"}:
            return await self._from_callback(raw)
        return await self._from_text(request.text or "")

    # ── مسار الأزرار ─────────────────────────────────────────────
    async def _from_callback(self, raw: str) -> AgentReply:
        parts = [part for part in raw.split(":") if part != ""]
        # ag : hadith : action : arg1 : arg2...
        action = parts[2].lower() if len(parts) > 2 else "random"
        args = parts[3:]
        if action == "get" and len(args) >= 2:
            return await self._send_hadith(args[0], args[1])
        if action == "section" and len(args) >= 2:
            return await self._send_section(args[0], args[1])
        if action == "search":
            query = _shortcuts.resolve(":".join(args).strip())
            return await self._search(query)
        if action == "daily":
            return await self._daily()
        if action == "random":
            return await self._random(args[0] if args else None)
        return await self._random()

    # ── مسار النص الحر ───────────────────────────────────────────
    async def _from_text(self, text: str) -> AgentReply:
        text = clean(text)
        if not text:
            return self._help()
        normalized = normalize_arabic(text)

        if any(pattern in normalized for pattern in _DAY_PATTERNS) or "حديث اليوم" in text:
            return await self._daily()

        key = hadith_api.find_collection(text)
        numbers = re.findall(r"\d+", _to_english_digits(text))

        if key and numbers:
            return await self._send_hadith(key, numbers[0])

        query = self._compose_query(text, key)
        if query:
            return await self._search(query)
        if key:
            return await self._random(key)
        return await self._search(text)

    @staticmethod
    def _compose_query(text: str, key: str | None) -> str:
        """يستخرج نصّ البحث من الجملة، ويحذف اسم المصدر إن وُجد."""
        match = _SEARCH_PREFIX.match(text)
        query = clean(text[match.end():]) if match else clean(_HADITH_LEAD.sub("", text, count=1))
        if not key:
            return query
        # إزالة اسم المصدر من نصّ البحث لتفادي البحث باسم الكتاب نفسه.
        normalized = normalize_arabic(query)
        stripped = normalized
        for alias in hadith_api._ALIASES.get(key, ()):  # noqa: SLF001 — ثوابت الوحدة
            stripped = stripped.replace(alias, " ")
        stripped = re.sub(r"\b(?:رقم|حديث|أحاديث|احاديث)\b", " ", stripped)
        return query if clean(stripped) else ""

    # ── بناء الردود ──────────────────────────────────────────────
    def _reply(self, text: str, *, buttons: list[list[Button]] | None = None, **meta) -> AgentReply:
        return AgentReply(
            text=text,
            agent=self.name,
            buttons=buttons or [],
            sources=[SOURCE_LABEL],
            meta=meta or {},
        )

    def _block(self, hadith: dict, *, max_len: int | None = None) -> str:
        number = to_arabic_digits(hadith.get("number", 0))
        lines = [f"<b>📜 {esc(hadith.get('collection', ''))} — حديث {esc(number)}</b>", DIVIDER]
        body = str(hadith.get("text") or "")
        if max_len and len(body) > max_len:
            body = body[:max_len].rstrip() + "…"
        lines.append(esc(body))

        grades = self._grades_text(hadith)
        if grades:
            lines.append(f"<i>الحكم: {esc(grades)}</i>")

        lines.append(f"<i>المصدر: {esc(hadith_api.reference_text(hadith))}</i>")
        book = hadith.get("book")
        if isinstance(book, int) and book > 0:
            lines.append(f"<i>رقم الكتاب: {esc(to_arabic_digits(book))}</i>")
        return "\n".join(lines)

    @staticmethod
    def _grades_text(hadith: dict) -> str:
        parts: list[str] = []
        for grade in hadith.get("grades") or []:
            if isinstance(grade, dict):
                name = str(grade.get("grade") or "").strip()
                who = str(grade.get("name") or "").strip()
                if name and who:
                    parts.append(f"{name} — {who}")
                elif name:
                    parts.append(name)
            elif grade:
                parts.append(str(grade))
        return "، ".join(parts)

    def _finalize(self, text: str, *, buttons=None, **meta) -> AgentReply:
        chunks = chunk(text)
        if len(chunks) > 1:
            meta["chunks"] = chunks
            meta["chunked"] = True
            text = text + "\n\n<i>(النص طويل — قُسِّم على أجزاء.)</i>"
            chunks = chunk(text)
            meta["chunks"] = chunks
        return self._reply(text, buttons=buttons, **meta)

    # ── السلوكيات ────────────────────────────────────────────────
    async def _daily(self) -> AgentReply:
        seed = int(date.today().strftime("%Y%m%d"))
        hadith = await hadith_api.random_hadith(seed=seed)
        header = "<b>🌟 حديث اليوم</b>\n"
        text = header + self._block(hadith)
        buttons = [[Button("🔄 حديث آخر", _callback("random"))]]
        return self._finalize(text, buttons=buttons, action="daily")

    async def _random(self, key: str | None = None) -> AgentReply:
        hadith = await hadith_api.random_hadith(key)
        text = "<b>🎲 حديث مختار</b>\n" + self._block(hadith)
        buttons = [[Button("🔄 حديث آخر", _callback("random"))]]
        return self._finalize(text, buttons=buttons, action="random")

    async def _send_hadith(self, key: str, number: str | int) -> AgentReply:
        try:
            number_int = int(_to_english_digits(str(number)))
        except (TypeError, ValueError):
            return self._not_found(key, str(number))
        try:
            hadith = await hadith_api.get_hadith(key, number_int)
        except Exception:
            return self._reply("تعذّر الوصول إلى هذا المصدر الآن. جرّب مصدراً آخر.")
        if not hadith or not hadith.get("text"):
            return self._not_found(key, number_int)

        text = self._block(hadith)
        collection_key = hadith.get("key", key)
        buttons = [
            [Button("🔄 حديث آخر", _callback("random"))],
            [Button(f"📚 {hadith.get('collection', '')}", _callback("random", collection_key))],
        ]
        return self._finalize(text, buttons=buttons, action="get", key=collection_key)

    async def _not_found(self, key: str, number) -> AgentReply:
        collection = hadith_api.COLLECTIONS.get(key, key)
        text = (
            f"<b>لم أجد الحديث رقم {esc(to_arabic_digits(number))} في {esc(collection)}.</b>\n"
            "تأكّد من الرقم، أو جرّب البحث بالمعنى:\n"
            "• «ابحث عن بر الوالدين»\n"
            "• «حديث عن النية»"
        )
        buttons = [[Button("🎲 حديث مختار", _callback("random"))]]
        return self._reply(text, buttons=buttons, action="not_found", key=key)

    async def _search(self, query: str) -> AgentReply:
        query = clean(query)
        if len(query) < 2:
            return self._help()
        try:
            results = await hadith_api.search_hadith(query, limit=5)
        except Exception:
            return self._reply(
                "تعذّر البحث في مصادر الحديث الآن. حاول بعد قليل، أو اكتب "
                "«البخاري 1» لعرض حديث برقمه."
            )

        title = f"<b>📚 أحاديث عن: {esc(query)}</b>"
        if not results:
            return self._no_results(query)

        blocks = [title, ""]
        buttons: list[list[Button]] = []
        for index, hadith in enumerate(results, start=1):
            blocks.append(f"<b>{esc(to_arabic_digits(index))})</b> {self._block(hadith, max_len=320)}")
            full_ref = hadith_api.reference_text(hadith)
            label = f"📖 {hadith.get('collection', '')} — {to_arabic_digits(hadith.get('number', 0))}"
            buttons.append(
                [
                    Button(label[:60], _callback("get", hadith.get("key", ""), str(hadith.get("number", 0)))),
                ]
            )
            blocks.append("")
        blocks.append(f"<i>المصدر: {esc(hadith_api.reference_text(results[0]))}</i>")
        buttons.append([Button("🔎 بحث أوسع في كل الكتب", _callback("search", query))])
        return self._finalize("\n".join(blocks), buttons=buttons, action="search", query=query)

    def _no_results(self, query: str) -> AgentReply:
        text = (
            f"<b>لم أجد حديثاً يطابق: {esc(query)}</b>\n\n"
            "جرّب كلمةً واحدة أو كلمتين من متن الحديث، أو اطلب حديثاً برقمه:\n"
            "• «البخاري 1»\n"
            "• «صحيح مسلم 4»\n"
            "• «حديث عن النية»\n"
            "• «حديث اليوم»"
        )
        buttons = [
            [Button("🔄 حديث مختار", _callback("random"))],
            [Button("🔎 بحث في كل الكتب", _callback("search", query))],
        ]
        return self._reply(text, buttons=buttons, action="no_results", query=query)

    async def _send_section(self, key: str, number: str | int) -> AgentReply:
        try:
            section = await hadith_api.book_section(key, int(_to_english_digits(str(number))))
        except Exception:
            return self._reply("تعذّر جلب هذا الباب الآن. جرّب بعد قليل.")
        hadiths = section.get("hadiths") or []
        if not hadiths:
            return self._not_found(key, number)

        header = (
            f"<b>📚 {esc(section.get('collection', ''))} — باب "
            f"{esc(to_arabic_digits(section.get('number', number)))}</b>"
        )
        if section.get("title"):
            header += f"\n<i>{esc(section['title'])}</i>"
        blocks = [header, DIVIDER, ""]
        buttons: list[list[Button]] = []
        for hadith in hadiths[:10]:
            blocks.append(self._block(hadith, max_len=280))
            buttons.append(
                [
                    Button(
                        f"📖 حديث {to_arabic_digits(hadith.get('number', 0))}",
                        _callback("get", key, str(hadith.get("number", 0))),
                    )
                ]
            )
            blocks.append("")
        return self._finalize("\n".join(blocks), buttons=buttons, action="section", key=key)

    def _help(self) -> AgentReply:
        text = (
            "<b>📜 وكيل الحديث</b>\n"
            "أعرض لك الأحاديث بنصوصها وتخريجها دون تحريف:\n"
            "• «حديث اليوم» — حديث مختار\n"
            "• «البخاري 1» أو «صحيح مسلم 4» — حديث برقمه\n"
            "• «ابحث عن بر الوالدين» — بحث في المتون\n"
            "• «النهي عن الغضب» — بحث مباشر"
        )
        return self._reply(text, action="help")
