"""وكيل الأذكار والأدعية — يقرأ ``app/data/adhkar.json`` (حصن المسلم).

المنطق النقي معزول في دوال على مستوى الوحدة (``load_data``, ``find_category``,
``find_categories``, ``render_item``, ``search_adhkar``) لتُختبر مباشرةً دون
تيليجرام أو شبكة، بينما ``AdhkarAgent`` يغلّفها في ردود ``AgentReply``.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from ..db import db
from ..models import AgentReply, AgentRequest, Button
from ..text import clean, esc, normalize_arabic, to_arabic_digits
from .base import BaseAgent

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "adhkar.json"
SOURCE_LABEL = "حصن المسلم — القحطاني"

# وسوم مناسبة للذكر العشوائي (تستثني الأبواب الجامدة/الفضائل البحثية).
RANDOM_TAGS = (
    "صباح", "مساء", "نوم", "استيقاظ", "هم", "مرض", "سفر",
    "طعام", "لباس", "مطر", "تسبيح", "رقية", "منزل", "مسجد", "وضوء",
)
# كلمات مفاتيح لقائمة الأدعية العامة.
DUA_KEYS = ("الهم", "الحزن", "الكرب", "السفر", "المريض", "المرض", "الدين", "القضاء", "الوالدين", "الوالد")

_DATA: dict | None = None
_LOADED_PATH: str | None = None
_SEARCH_INDEX: list[dict] | None = None
# الوسوم المُتاحة ⇒ معرّفات الأبواب.
_TAG_INDEX: dict[str, list[int]] = {}


# ── تحميل البيانات ─────────────────────────────────────────────────
def load_data(path: str | Path | None = None, *, refresh: bool = False) -> dict:
    """يحمّل بيانات الأذكار ويهيّئ فهرس البحث في الذاكرة مرة واحدة."""
    global _DATA, _LOADED_PATH, _SEARCH_INDEX, _TAG_INDEX
    target = Path(path) if path is not None else DATA_PATH
    key = str(target)
    if _DATA is not None and not refresh and _LOADED_PATH == key:
        return _DATA
    if not target.exists():
        raise FileNotFoundError(f"ملف الأذكار غير موجود: {target}")

    raw = json.loads(target.read_text(encoding="utf-8"))
    categories = raw.get("categories") or []

    index: list[dict] = []
    tag_index: dict[str, list[int]] = {}
    for cat in categories:
        for tag in cat.get("tags") or []:
            tag_index.setdefault(tag, []).append(cat["id"])
        for position, item in enumerate(cat.get("items") or [], start=1):
            index.append(
                {
                    "cat": cat,
                    "item": item,
                    "index": position,
                    "norm": normalize_arabic(item.get("text") or ""),
                }
            )

    _DATA = raw
    _LOADED_PATH = key
    _SEARCH_INDEX = index
    _TAG_INDEX = tag_index
    return _DATA


def reset_cache() -> None:
    """يفرّغ الذاكرة المؤقتة (تُستعمل في الاختبارات)."""
    global _DATA, _LOADED_PATH, _SEARCH_INDEX, _TAG_INDEX
    _DATA = None
    _LOADED_PATH = None
    _SEARCH_INDEX = None
    _TAG_INDEX = {}


def data_available(path: str | Path | None = None) -> bool:
    return (Path(path) if path is not None else DATA_PATH).exists()


# ── استعلامات الأبواب ──────────────────────────────────────────────
def _score(query_n: str, title_n: str) -> float:
    if not query_n or not title_n:
        return 0.0
    if query_n == title_n:
        return 100.0
    score = 0.0
    contains = query_n in title_n
    if contains:
        score += 10.0 + 10.0 * len(query_n) / max(len(title_n), 1)
    qwords = [w for w in query_n.split() if len(w) >= 2]
    twords = title_n.split()
    overlap = sum(1 for w in qwords if w in twords)
    score += overlap * 4.0
    if not contains and overlap == 0:
        return 0.0
    score -= len(twords) * 0.05
    return score


def rank_categories(query: str, limit: int | None = None) -> list[tuple[float, dict]]:
    """يعيد (الدرجة، الباب) مرتّبة تنازلياً لأفضل مطابقة."""
    load_data()
    query_n = normalize_arabic(query)
    if len(query_n) < 2:
        return []
    ranked: list[tuple[float, dict]] = []
    for cat in _DATA["categories"]:
        score = _score(query_n, normalize_arabic(cat.get("title") or ""))
        if score > 0:
            ranked.append((score, cat))
    ranked.sort(key=lambda pair: (-pair[0], len(pair[1].get("title") or ""), pair[1]["id"]))
    return ranked[:limit] if limit else ranked


def find_categories(query: str, limit: int = 8) -> list[dict]:
    """أبواب مطابقة للكلمة المفتاحية (حتى ``limit``)."""
    return [cat for _, cat in rank_categories(query, limit=limit)]


def find_category(query: str) -> dict | None:
    """أفضل باب مطابق أو ``None``."""
    ranked = rank_categories(query, limit=1)
    return ranked[0][1] if ranked else None


def get_category(cat_id: int) -> dict | None:
    load_data()
    for cat in _DATA["categories"]:
        if int(cat["id"]) == int(cat_id):
            return cat
    return None


def get_item(cat_id: int, index: int) -> dict | None:
    """العنصر رقم ``index`` (1-based) في الباب، أو ``None``."""
    cat = get_category(cat_id)
    if not cat:
        return None
    items = cat.get("items") or []
    if 1 <= index <= len(items):
        return items[index - 1]
    return None


def item_count(cat_id: int) -> int:
    cat = get_category(cat_id)
    return len(cat.get("items") or []) if cat else 0


# ── العرض ──────────────────────────────────────────────────────────
def _count_phrase(count: int) -> str:
    count = max(int(count or 1), 1)
    if count == 1:
        return "يُقال مرة واحدة"
    if count == 2:
        return "يُقال مرتان"
    if count <= 10:
        return f"يُقال {to_arabic_digits(count)} مرات"
    return f"يُقال {to_arabic_digits(count)} مرة"


def render_item(cat_id: int, index: int) -> str | None:
    """يعيد نص الذكر بتنسيق HTML (أو ``None`` خارج الحدود)."""
    cat = get_category(cat_id)
    item = get_item(cat_id, index)
    if not cat or not item:
        return None
    total = len(cat.get("items") or [])
    return "\n".join(
        [
            f"📖 <b>{esc(cat['title'])}</b>",
            f"<i>الذكر {to_arabic_digits(index)} من {to_arabic_digits(total)}</i>",
            "",
            esc(item.get("text") or ""),
            "",
            f"🔁 <b>{_count_phrase(item.get('count') or 1)}</b>",
            "",
            "<i>المصدر: حصن المسلم</i>",
        ]
    )


# ── البحث النصي ────────────────────────────────────────────────────
def search_adhkar(query: str, limit: int = 5) -> list[dict]:
    """يبحث في متون كل الأذكار (بعد التطبيع) ويعيد أفضل النتائج."""
    load_data()
    query_n = normalize_arabic(query)
    if len(query_n) < 2:
        return []
    tokens = [t for t in query_n.split() if len(t) >= 2]

    results: list[tuple[float, dict]] = []
    for entry in _SEARCH_INDEX or []:
        norm = entry["norm"]
        if query_n in norm:
            position = norm.find(query_n)
            score = 100.0 - position * 0.1 + 10.0 * len(query_n) / max(len(norm), 1)
        elif tokens:
            matched = [token for token in tokens if token in norm]
            if len(matched) == len(tokens):
                score = 40.0 + 5.0 * len(tokens)
            elif matched:
                score = 10.0 * len(matched) + 10.0 * len(matched) / len(tokens)
            else:
                continue
        else:
            continue
        results.append((score, entry))

    results.sort(key=lambda pair: (-pair[0], len(pair[1]["norm"])))
    return [
        {"category": entry["cat"], "item": entry["item"], "index": entry["index"]}
        for _, entry in results[:limit]
    ]


# ── الوكيل ─────────────────────────────────────────────────────────
class AdhkarAgent(BaseAgent):
    name = "adhkar"
    title = "وكيل الأذكار"
    priority = 70
    description = "أذكار وأدعية من حصن المسلم: الصباح والمساء، النوم، السفر، الهم، التسبيح والرقية."
    keywords = [
        "ذكر", "أذكار", "اذكار", "دعاء", "أدعية", "ادعيه", "تسبيح", "سبحة",
        "استغفار", "حصن المسلم", "أذكار الصباح", "أذكار المساء", "أذكار النوم",
        "الصباح", "المساء", "الهم", "الكرب", "الرقية", "رقية",
    ]
    examples = [
        "أذكار الصباح", "أذكار المساء", "أذكار النوم",
        "دعاء الهم والحزن", "سبحان الله", "دعاء السفر",
    ]

    # ── أدوات داخلية ──────────────────────────────────────────────
    def _reply(self, text: str, **kwargs: Any) -> AgentReply:
        kwargs.setdefault("agent", self.name)
        kwargs.setdefault("sources", [SOURCE_LABEL])
        return AgentReply(text=text, **kwargs)

    @staticmethod
    def _callback_data(request: AgentRequest) -> str:
        """يستخرج معرّف الزر من الطلب (args/raw_command/text)."""
        for candidate in (request.args, request.raw_command, request.text):
            value = (candidate or "").strip()
            if value.startswith("ag:adhkar:"):
                return value
            found = re.search(r"ag:adhkar:[0-9A-Za-z_]+(?::[0-9]+)*", value)
            if found:
                return found.group(0)
        return ""

    def _buttons_item(self, cat_id: int, index: int, item: dict) -> list[list[Button]]:
        rows: list[list[Button]] = [
            [
                Button("◀️ السابق", f"ag:adhkar:prev:{cat_id}:{index}"),
                Button("التالي ▶️", f"ag:adhkar:next:{cat_id}:{index}"),
            ]
        ]
        second: list[Button] = []
        if item.get("audio"):
            second.append(Button("🔊 صوت", f"ag:adhkar:audio:{cat_id}:{index}"))
        second.append(Button("📖 المصدر", f"ag:adhkar:source:{cat_id}:{index}"))
        if second:
            rows.append(second)
        rows.append(
            [
                Button("📚 الأبواب", f"ag:adhkar:cat:{cat_id}"),
                Button("🎲 ذكر عشوائي", "ag:adhkar:random"),
            ]
        )
        return rows

    def _show_item(self, cat_id: int, index: int) -> AgentReply:
        cat = get_category(cat_id)
        total = item_count(cat_id)
        if not cat or total == 0:
            return self._fallback(str(cat_id))
        index = max(1, min(int(index), total))
        item = get_item(cat_id, index) or {}
        text = render_item(cat_id, index) or ""
        return self._reply(
            text,
            buttons=self._buttons_item(cat_id, index, item),
            meta={"category_id": cat_id, "index": index, "total": total},
        )

    def _category_page(self, cat_id: int) -> AgentReply:
        cat = get_category(cat_id)
        if not cat:
            return self._fallback(str(cat_id))
        items = cat.get("items") or []
        rows: list[list[Button]] = []
        row: list[Button] = []
        for position in range(1, min(len(items), 20) + 1):
            row.append(Button(to_arabic_digits(position), f"ag:adhkar:item:{cat_id}:{position}"))
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Button("🎲 ذكر عشوائي", "ag:adhkar:random")])
        text = "\n".join(
            [
                f"📚 <b>{esc(cat['title'])}</b>",
                f"<i>عدد الأذكار: {to_arabic_digits(len(items))}</i>",
                "",
                "اختر ذكراً من الأزرار، أو ابدأ بالأول:",
                f"➡️ <code>ag:adhkar:item:{cat_id}:1</code>",
            ]
        )
        return self._reply(text, buttons=rows, meta={"category_id": cat_id})

    def _category_list(self, cats: list[dict], query: str = "") -> AgentReply:
        rows: list[list[Button]] = []
        row: list[Button] = []
        for cat in cats[:8]:
            row.append(Button(cat["title"][:40], f"ag:adhkar:cat:{cat['id']}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Button("🎲 ذكر عشوائي", "ag:adhkar:random")])
        header = f"🔎 أبواب مطابقة لـ «{esc(query)}»:" if query else "📚 اختر باباً:"
        return self._reply(header, buttons=rows)

    def _source(self, cat_id: int, index: int = 0) -> AgentReply:
        cat = get_category(cat_id)
        title = esc(cat["title"]) if cat else "—"
        text = "\n".join(
            [
                "📖 <b>المصدر</b>",
                "",
                f"الباب: {title}",
                f"المرجع: <i>{esc(SOURCE_LABEL)}</i>",
                "",
                "المتن مأخوذ من «حصن المسلم» للشيخ سعيد بن علي بن وهف القحطاني.",
            ]
        )
        return self._reply(text)

    def _audio(self, cat_id: int, index: int) -> AgentReply:
        cat = get_category(cat_id)
        item = get_item(cat_id, index)
        if not cat or not item or not item.get("audio"):
            return self._reply("🔇 لا يتوفر تسجيل صوتي لهذا الذكر.")
        reply = self._show_item(cat_id, index)
        reply.audio_url = item["audio"]
        reply.audio_title = cat["title"]
        return reply

    def _random(self) -> AgentReply:
        load_data()
        pool = [
            cat
            for cat in _DATA["categories"]
            if any(tag in RANDOM_TAGS for tag in (cat.get("tags") or []))
        ] or _DATA["categories"]
        cat = random.choice(pool)
        index = random.randint(1, max(1, len(cat.get("items") or [])))
        return self._show_item(cat["id"], index)

    def _dua_menu(self) -> AgentReply:
        load_data()
        found: list[dict] = []
        seen: set[int] = set()
        for cat in _DATA["categories"]:
            title_n = normalize_arabic(cat.get("title") or "")
            if cat["id"] in seen:
                continue
            if any(normalize_arabic(key) in title_n for key in DUA_KEYS):
                found.append(cat)
                seen.add(cat["id"])
            if len(found) >= 8:
                break
        if not found:
            return self._menu()
        return self._category_list(found, query="أدعية")

    def _fallback(self, text: str) -> AgentReply:
        text_disp = esc(clean(text)[:60]) if text else ""
        body = [
            f"لم أجد باباً مطابقاً لـ «{text_disp}»." if text_disp else "لم أفهم الطلب.",
            "جرّب: أذكار الصباح، أذكار النوم، دعاء السفر، أو اكتب «ابحث عن ذكر …».",
        ]
        return self._reply("\n".join(body), buttons=self._menu_buttons())

    def _menu_buttons(self) -> list[list[Button]]:
        picks = ["الصباح", "النوم", "السفر", "الهم والحزن", "المساء", "الكرب"]
        rows: list[list[Button]] = []
        row: list[Button] = []
        for query in picks:
            cat = find_category(query)
            if not cat:
                continue
            row.append(Button(cat["title"][:32], f"ag:adhkar:cat:{cat['id']}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Button("📿 سبّح", "ag:adhkar:t_tasbih"), Button("🎲 ذكر عشوائي", "ag:adhkar:random")])
        return rows

    def _menu(self) -> AgentReply:
        text = "\n".join(
            [
                "🕌 <b>وكيل الأذكار</b>",
                "",
                "اختر باباً، أو اكتب طلبك مباشرة:",
                "• أذكار الصباح / أذكار النوم",
                "• دعاء السفر / دعاء الهم والحزن",
                "• ابحث عن ذكر …",
                "• تسبيح (لعدّاد السبحة)",
            ]
        )
        return self._reply(text, buttons=self._menu_buttons())

    # ── عدّاد التسبيح ─────────────────────────────────────────────
    @staticmethod
    def _is_tasbih(norm: str) -> bool:
        return norm in {"تسبيح", "سبحه", "سبح", "سبحان", "سبحان الله"} or norm.startswith("سبحان")

    def _tasbih_view(self, count: int) -> AgentReply:
        text = "\n".join(
            [
                "📿 <b>السبحة</b>",
                "",
                f"عدد تسبيحاتك: <b>{to_arabic_digits(count)}</b>",
                "",
                "<i>سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.</i>",
                "",
                "<i>المصدر: حصن المسلم</i>",
            ]
        )
        buttons = [
            [Button("📿 سبّح", "ag:adhkar:t_tasbih"), Button("♻️ تصفير", "ag:adhkar:reset")]
        ]
        return self._reply(text, buttons=buttons, meta={"tasbih": count})

    async def _tasbih(self, request: AgentRequest) -> AgentReply:
        count = await db.bump_counter(request.chat_id, "tasbih", 1)
        return self._tasbih_view(count)

    async def _reset_tasbih(self, request: AgentRequest) -> AgentReply:
        current = await db.get_counter(request.chat_id, "tasbih")
        if current:
            await db.bump_counter(request.chat_id, "tasbih", -current)
        return self._tasbih_view(0)

    # ── البحث ─────────────────────────────────────────────────────
    def _search(self, query: str) -> AgentReply:
        results = search_adhkar(query, limit=5)
        if not results:
            return self._fallback(query)
        lines = [f"🔎 <b>نتائج البحث عن «{esc(clean(query))}»</b>", ""]
        rows: list[list[Button]] = []
        for position, hit in enumerate(results, start=1):
            snippet = clean(hit["item"].get("text") or "")
            if len(snippet) > 90:
                snippet = snippet[:90] + "…"
            lines.append(f"{to_arabic_digits(position)}. <i>{esc(hit['category']['title'])}</i>")
            lines.append(esc(snippet))
            lines.append("")
            rows.append(
                [Button(f"{to_arabic_digits(position)} — {hit['category']['title'][:28]}",
                        f"ag:adhkar:item:{hit['category']['id']}:{hit['index']}")]
            )
        return self._reply("\n".join(lines).strip(), buttons=rows)

    # ── الأزرار ───────────────────────────────────────────────────
    async def _on_callback(self, payload: str, request: AgentRequest) -> AgentReply:
        parts = payload.split(":")
        action = parts[2] if len(parts) > 2 else ""
        args = parts[3:]

        def arg(pos: int, default: int = 0) -> int:
            try:
                return int(args[pos])
            except (IndexError, ValueError):
                return default

        if action == "cat":
            return self._category_page(arg(0))
        if action == "item":
            return self._show_item(arg(0), arg(1, 1))
        if action == "next":
            cat_id, index = arg(0), arg(1, 1)
            return self._show_item(cat_id, index + 1)
        if action == "prev":
            cat_id, index = arg(0), arg(1, 1)
            return self._show_item(cat_id, index - 1)
        if action == "audio":
            return self._audio(arg(0), arg(1, 1))
        if action == "source":
            return self._source(arg(0), arg(1))
        if action == "t_tasbih":
            return await self._tasbih(request)
        if action == "reset":
            return await self._reset_tasbih(request)
        if action == "random":
            return self._random()
        return self._menu()

    # ── المدخل ────────────────────────────────────────────────────
    async def handle(self, request: AgentRequest) -> AgentReply:
        payload = self._callback_data(request)
        if payload:
            return await self._on_callback(payload, request)

        text = (request.text or "").strip()
        try:
            load_data()
        except FileNotFoundError:
            return self._reply(
                "⚙️ بيانات الأذكار غير مثبّتة بعد.\n"
                "شغّل الأمر: <code>python scripts/fetch_adhkar.py</code>"
            )

        norm = normalize_arabic(text)
        if not norm:
            return self._menu()
        if self._is_tasbih(norm):
            return await self._tasbih(request)
        if "عشوائي" in norm or "ذكرني" in norm:
            return self._random()
        if norm.startswith("ابحث") or "ابحث عن" in norm:
            query = re.sub(r"^ابحث\s*(عن)?\s*", "", norm).strip()
            # «ذكر» كلمة حشو شائعة في الطلب (ابحث عن ذكر الصلاة).
            query = re.sub(r"^ذكر\s+", "", query).strip()
            return self._search(query or text)
        if norm in {"دعاء", "ادعيه", "دعاء عام", "ادعيه عامه"}:
            return self._dua_menu()

        ranked = rank_categories(text, limit=8)
        if not ranked:
            return self._fallback(text)
        top_score, best = ranked[0]
        if len(ranked) == 1 or top_score >= ranked[1][0] + 3.0:
            return self._show_item(best["id"], 1)
        return self._category_list([cat for _, cat in ranked], query=text)


# تُستعمل من السجلّ (registry) لاحقاً.
__all__ = [
    "AdhkarAgent",
    "load_data",
    "reset_cache",
    "find_category",
    "find_categories",
    "rank_categories",
    "render_item",
    "search_adhkar",
    "get_category",
    "get_item",
    "item_count",
    "SOURCE_LABEL",
]
