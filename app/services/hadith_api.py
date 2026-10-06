"""واجهة الحديث — fawazahmed0/hadith-api عبر jsDelivr (بلا مفتاح، نصوص عربية).

المبادئ:
- لا اختلاق ولا تحريف: النصّ يُنقل كما هو من المصدر حرفاً بحرف.
- تخزين الكتاب الكامل على القرص بعد أول تنزيل (يعمل بعدها بلا شبكة).
- كل دالة تعيد صيغة موحّدة مُطبَّعة (انظر `_normalize`).
- قفل لكل كتاب (asyncio.Lock) حتى لا يتنزّل الكتاب نفسه مرّتين بالتوازي.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..config import BASE_DIR
from ..text import normalize_arabic, to_arabic_digits
from .http import http

BASE = "https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1"

# مجلد الكاش — متغيّر وحدة قابل للاستبدال في الاختبارات (يُقرأ لحظة التنفيذ).
CACHE_DIR: Path = BASE_DIR / "data" / "cache" / "hadith"

COLLECTIONS: dict[str, str] = {
    "bukhari": "صحيح البخاري",
    "muslim": "صحيح مسلم",
    "abudawud": "سنن أبي داود",
    "tirmidhi": "جامع الترمذي",
    "nasai": "سنن النسائي",
    "ibnmajah": "سنن ابن ماجه",
    "malik": "موطأ مالك",
    "nawawi": "الأربعون النووية",
    "qudsi": "الأحاديث القدسية",
}

# الكتب الأساسية: حديث اليوم + البحث الافتراضي (تفاديًا لتنزيل كل المصادر).
CORE_COLLECTIONS = ("bukhari", "muslim", "nawawi")
DAILY_WEIGHTS = {"bukhari": 4, "muslim": 4, "nawawi": 2}
DEFAULT_SEARCH = CORE_COLLECTIONS

# ── مطابقة أسماء المصادر ────────────────────────────────────────────
# القيم الخام ثم نسخة مُطبَّعة (normalize_arabic) يُقارَن بها؛ الأطول يُطابق أولاً.
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "bukhari": ("صحيح البخاري", "البخاري", "بخاري", "sahih al bukhari", "bukhari"),
    "muslim": ("صحيح مسلم", "مسلم", "sahih muslim", "muslim"),
    "abudawud": (
        "سنن ابي داود", "سنن ابو داود", "ابي داود", "ابو داود", "داود",
        "sunan abu dawud", "abu dawud", "abudawud",
    ),
    "tirmidhi": ("جامع الترمذي", "سنن الترمذي", "الترمذي", "ترمذي", "tirmidhi"),
    "nasai": ("سنن النسائي", "النسائي", "نسائي", "nasai"),
    "ibnmajah": ("سنن ابن ماجه", "ابن ماجه", "ابن ماجه", "ماجه", "ibn majah", "ibnmajah"),
    "malik": ("موطا مالك", "الموطا", "موطا", "مالك", "malik", "muwatta"),
    "nawawi": (
        "الاربعون النوويه", "الاربعين النوويه", "الاحاديث النوويه", "النوويه",
        "النووي", "نووي", "الاربعون", "الاربعين", "nawawi",
    ),
    "qudsi": ("الاحاديث القدسيه", "القدسيه", "قدسي", "حديث قدسي", "qudsi"),
}

_ALIASES: dict[str, tuple[str, ...]] = {
    key: tuple(normalize_arabic(alias).lower() for alias in aliases)
    for key, aliases in _RAW_ALIASES.items()
}

_ALL_ALIASES: list[tuple[str, str]] = sorted(
    ((alias, key) for key, aliases in _ALIASES.items() for alias in aliases),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def find_collection(query: str) -> str | None:
    """يطابق اسم مصدر عربي/إنجليزي إلى مفتاح الكتاب، أو None."""
    if not query:
        return None
    normalized = normalize_arabic(query).lower()
    if not normalized:
        return None
    # المفتاح الإنجليزي ككلمة مستقلة
    for key in COLLECTIONS:
        if key in normalized.split() or normalized == key:
            return key
    for alias, key in _ALL_ALIASES:
        if alias in normalized:
            return key
    return None


def collection_title(key: str) -> str:
    return COLLECTIONS.get(key, key)


# ── حالة الوحدة: أقفال + كاش ذاكرة محدود ────────────────────────────
_LOCKS: dict[str, asyncio.Lock] = {}
_BOOKS: "OrderedDict[str, dict]" = OrderedDict()
_SEARCH_INDEX: "OrderedDict[str, list[tuple[int, str]]]" = OrderedDict()
_BOOKS_MAX = 3
_INDEX_MAX = 2


def _lock_for(key: str) -> asyncio.Lock:
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _norm_key(key: str) -> str:
    key = (key or "").strip().lower()
    if key in COLLECTIONS:
        return key
    found = find_collection(key)
    if found:
        return found
    raise ValueError(f"مصدر حديث غير معروف: {key!r}")


def _cache_path(key: str) -> Path:
    return Path(CACHE_DIR) / f"ara-{key}.json"


def clear_cache() -> None:
    """يفرّغ كاش الذاكرة (لا يمسّ ملفات القرص) — يُستعمل في الاختبارات."""
    _BOOKS.clear()
    _SEARCH_INDEX.clear()


# ── قراءة/كتابة القرص ──────────────────────────────────────────────
def _read_json_file(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)


async def load_book(key: str) -> dict:
    """ينزّل الكتاب الكامل ويخزّنه على القرص، أو يقرأه من القرص إن وُجد بلا شبكة."""
    key = _norm_key(key)
    path = _cache_path(key)

    cached = await asyncio.to_thread(_read_json_file, path)
    if cached is not None:
        return cached

    async with _lock_for(key):
        # فحص ثانٍ داخل القفل: قد يكون كتابٌ آخر قد نزّله للتوّ.
        cached = await asyncio.to_thread(_read_json_file, path)
        if cached is not None:
            return cached
        url = f"{BASE}/editions/ara-{key}.json"
        data = await http.get_json(url, ttl=86400)
        if not isinstance(data, dict) or "hadiths" not in data:
            raise ValueError(f"ملف الكتاب غير صالح: {key}")
        await asyncio.to_thread(_write_json_file, path, data)
        return data


async def _loaded(key: str) -> dict:
    """نسخة مخزّنة في الذاكرة (LRU محدود) من الكتاب."""
    key = _norm_key(key)
    if key in _BOOKS:
        _BOOKS.move_to_end(key)
        return _BOOKS[key]
    data = await load_book(key)
    _BOOKS[key] = data
    _BOOKS.move_to_end(key)
    while len(_BOOKS) > _BOOKS_MAX:
        _BOOKS.popitem(last=False)
    return data


# ── تطبيع الأحاديث ─────────────────────────────────────────────────
def _normalize(hadith: dict, key: str) -> dict:
    reference = hadith.get("reference") or {}
    number = hadith.get("hadithnumber")
    if number is None:
        number = hadith.get("arabicnumber")
    book = reference.get("book")
    hadith_ref = reference.get("hadith")
    grades = hadith.get("grades") or []
    return {
        "key": key,
        "collection": collection_title(key),
        "number": int(number) if number is not None else 0,
        "arabicnumber": hadith.get("arabicnumber"),
        "text": str(hadith.get("text") or "").strip(),
        "grades": list(grades),
        "book": int(book) if isinstance(book, int) else None,
        "hadith_ref": int(hadith_ref) if isinstance(hadith_ref, int) else None,
    }


async def sections(key: str) -> dict[str, str]:
    """عناوين الأبواب من ميتاداتا الكتاب: {رقم الباب: العنوان}."""
    book = await _loaded(key)
    raw = ((book.get("metadata") or {}).get("sections")) or {}
    return {str(number): str(title) for number, title in raw.items()}


async def get_hadith(key: str, number: int) -> dict | None:
    """يبحث عن حديث برقمه (hadithnumber ثم arabicnumber) داخل كتاب مُحمَّل."""
    key = _norm_key(key)
    try:
        number = int(number)
    except (TypeError, ValueError):
        return None
    book = await _loaded(key)
    for hadith in book.get("hadiths") or []:
        if hadith.get("hadithnumber") == number or hadith.get("arabicnumber") == number:
            return _normalize(hadith, key)
    return None


async def random_hadith(key: str | None = None, *, seed: int | None = None) -> dict:
    """حديث عشوائي؛ إن غاب المفتاح فمن الكتب الأساسية بأوزان. `seed` لثبات يومي."""
    rng = random.Random(seed) if seed is not None else random
    if key is None:
        keys = list(DAILY_WEIGHTS)
        weights = [DAILY_WEIGHTS[k] for k in keys]
        key = rng.choices(keys, weights=weights, k=1)[0]
    key = _norm_key(key)
    book = await _loaded(key)
    hadiths = [h for h in (book.get("hadiths") or []) if str(h.get("text") or "").strip()]
    if not hadiths:
        raise ValueError(f"لا توجد أحاديث في {key}")
    chosen = hadiths[rng.randrange(len(hadiths))]
    return _normalize(chosen, key)


def _build_index(book: dict) -> list[tuple[int, str]]:
    index: list[tuple[int, str]] = []
    for hadith in book.get("hadiths") or []:
        text = str(hadith.get("text") or "").strip()
        if not text:
            continue
        number = hadith.get("hadithnumber")
        if number is None:
            number = hadith.get("arabicnumber") or 0
        index.append((int(number), normalize_arabic(text)))
    return index


async def _search_index(key: str) -> list[tuple[int, str]]:
    key = _norm_key(key)
    if key in _SEARCH_INDEX:
        _SEARCH_INDEX.move_to_end(key)
        return _SEARCH_INDEX[key]
    book = await _loaded(key)
    index = await asyncio.to_thread(_build_index, book)
    _SEARCH_INDEX[key] = index
    _SEARCH_INDEX.move_to_end(key)
    while len(_SEARCH_INDEX) > _INDEX_MAX:
        _SEARCH_INDEX.popitem(last=False)
    return index


async def search_hadith(query: str, key: str | None = None, limit: int = 5) -> list[dict]:
    """بحث محلي في النصوص (بعد تطبيع الألف/الهمزة/التشكيل). يقبل كلمات متعددة.

    يطابق كل الكلمات إن أمكن، وإلا يرتّب النتائج حسب عدد الكلمات المطابقة.
    """
    tokens = [token for token in normalize_arabic(query or "").split() if token]
    if not tokens:
        return []
    keys = [_norm_key(key)] if key else list(DEFAULT_SEARCH)

    scored: list[tuple[int, str, int, int]] = []
    for collection_key in keys:
        try:
            index = await _search_index(collection_key)
        except Exception:  # مصدر تعذّر تنزيله لا يُسقط البحث كله
            continue
        for position, (number, normalized_text) in enumerate(index):
            if not normalized_text:
                continue
            score = sum(1 for token in tokens if token in normalized_text)
            if score:
                scored.append((score, collection_key, number, position))
    if not scored:
        return []

    best = [hit for hit in scored if hit[0] == len(tokens)]
    ranked = best or scored
    order = {collection_key: index for index, collection_key in enumerate(keys)}
    ranked.sort(key=lambda item: (-item[0], order.get(item[1], 999), item[3]))

    results: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for _, collection_key, number, _ in ranked:
        if len(results) >= limit:
            break
        marker = (collection_key, number)
        if marker in seen:
            continue
        seen.add(marker)
        item = await get_hadith(collection_key, number)
        if item and item["text"]:
            results.append(item)
    return results


async def book_section(key: str, number: int) -> dict:
    """يجلب باباً واحداً فقط (خفيف) بدل الكتاب كامله."""
    key = _norm_key(key)
    number = int(number)
    url = f"{BASE}/editions/ara-{key}/{number}.json"
    data = await http.get_json(url, ttl=86400)
    if not isinstance(data, dict):
        raise ValueError(f"باب غير صالح: {key}/{number}")
    metadata = data.get("metadata") or {}
    section_titles = metadata.get("section") or {}
    title = ""
    for value in section_titles.values():
        title = str(value)
        break
    hadiths = [_normalize(hadith, key) for hadith in (data.get("hadiths") or [])]
    return {
        "key": key,
        "collection": collection_title(key),
        "number": number,
        "title": title,
        "section": title,
        "hadiths": hadiths,
    }


def reference_text(hadith: dict) -> str:
    """«صحيح البخاري — حديث رقم ١» بالأرقام العربية."""
    collection = hadith.get("collection") or collection_title(hadith.get("key", ""))
    number = to_arabic_digits(hadith.get("number", 0))
    return f"{collection} — حديث رقم {number}"
