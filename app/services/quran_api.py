"""خدمة القرآن الكريم — بيانات محلية + شبكة عند الحاجة.

المصدر الأساسي: ملف `app/data/quran_uthmani.json` (يُنتجه scripts/fetch_quran.py)
عبر api.alquran.cloud برواية حفص. لا يُختلق أي نصّ: كل آية/تفسير يأتي من
الملف أو من الشبكة، وعند الفشل يرفع استثناءً يلتقطه الوكيل ويحوّله لرسالة مهذّبة.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..text import normalize_arabic
from .http import http

BASE = "https://api.alquran.cloud/v1"
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "quran_uthmani.json"

SOURCE_QURAN = "القرآن الكريم — رواية حفص (api.alquran.cloud)"
SOURCE_TAFSIR = "التفسير الميسر — مجمع الملك فهد"

RECITERS: dict[str, str] = {
    "ar.alafasy": "مشاري العفاسي",
    "ar.husary": "محمود خليل الحصري",
    "ar.abdulbasitmurattal": "عبد الباسط (مرتل)",
    "ar.minshawi": "المنشاوي",
    "ar.muhammadayyoub": "محمد أيوب",
}

TAFSIR_KINDS: dict[str, str] = {
    "ar.muyassar": "التفسير الميسر",
    "ar.jalalayn": "تفسير الجلالين",
    "ar.qurtubi": "تفسير القرطبي",
    "ar.baghawi": "تفسير البغوي",
    "ar.waseet": "التفسير الوسيط",
}

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u08F0-\u08FF]")
_LONG_VOWELS = re.compile(r"[اويى]")   # لتسامح الرسم العثماني (الصلاة/الصلوة)


def _loose(normalized: str) -> str:
    """صيغة متسامحة: حذف حروف العلّة لمواجهة اختلاف الرسم العثماني."""
    return _LONG_VOWELS.sub("", normalized)


# ── تحويل بيانات الشبكة إلى الشكل المحلي ────────────────────────────
def _clean(text: str) -> str:
    return " ".join(str(text or "").replace("\ufeff", "").replace("\u200b", "").split())


def _surah_name(text: str) -> str:
    name = _clean(text)
    for prefix in ("سُورَةُ ", "سورة ", "سُورَة "):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    name = _DIACRITICS.sub("", name).replace("\u0640", "").replace("\u0671", "\u0627")
    return " ".join(name.split())


def normalize_payload(raw: Any) -> dict:
    """يحوّل استجابة api.alquran.cloud إلى الشكل المحلي الموحّد."""
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or not data.get("surahs"):
        raise ValueError("استجابة القرآن غير صالحة")

    surahs: list[dict] = []
    for surah in data["surahs"]:
        ayahs = [
            _clean(a.get("text", ""))
            for a in sorted(surah.get("ayahs", []), key=lambda x: x.get("numberInSurah", 0))
        ]
        surahs.append(
            {
                "number": int(surah.get("number", len(surahs) + 1)),
                "name": _surah_name(surah.get("name", "")),
                "englishName": _clean(surah.get("englishName", "")),
                "revelationType": (surah.get("revelationType") or "").strip(),
                "ayahs": ayahs,
            }
        )
    return {"source": SOURCE_QURAN, "surahs": surahs}


async def _fetch_remote() -> dict:
    return normalize_payload(await http.get_json(f"{BASE}/quran/quran-uthmani"))


# ── تحميل البيانات ─────────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_quran() -> dict:
    """يحمّل الملف مرة واحدة في الذاكرة؛ وإن غاب يجلبه من الشبكة."""
    if DATA_FILE.exists():
        try:
            payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("surahs"):
                return payload
        except (OSError, ValueError):  # pragma: no cover - ملف تالف
            pass
    raise QuranDataMissing(
        "ملف بيانات القرآن غير موجود. شغّل: python scripts/fetch_quran.py"
    )


class QuranDataMissing(FileNotFoundError):
    """بيانات القرآن المُجمَّعة مفقودة."""


async def ensure_quran() -> dict:
    """يضمن توفّر بيانات القرآن؛ ويجلبها من الشبكة إن غابت (سياق غير متزامن)."""
    try:
        return load_quran()
    except FileNotFoundError:
        payload = await _fetch_remote()
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        reload()
        return load_quran()


def reload() -> dict:
    """يفرّغ الذاكرة ويزامن تذكيراً جديداً (يُفيد الاختبارات والتحديث)."""
    load_quran.cache_clear()
    _index.cache_clear()
    return load_quran()


# ── واجهات القراءة ─────────────────────────────────────────────────
def surah_list() -> list[dict]:
    return [
        {
            "number": s["number"],
            "name": s["name"],
            "englishName": s["englishName"],
            "ayahs_count": len(s["ayahs"]),
        }
        for s in load_quran()["surahs"]
    ]


def _by_number(number: int) -> dict | None:
    surahs = load_quran()["surahs"]
    if 1 <= number <= len(surahs):
        return surahs[number - 1]
    return None


def _english_keys(text: str) -> set[str]:
    """مفاتيح إنجليزية متسامحة: تجرّد الرموز، توحيد الحروف المكرّرة، ونزع «al»."""
    base = re.sub(r"[^a-z]", "", str(text).lower())
    collapsed = re.sub(r"(.)\1+", r"\1", base)
    keys = {base, collapsed}
    for variant in (base, collapsed):
        if variant.startswith("al"):
            keys.add(variant[2:])
    return {k for k in keys if k}


@lru_cache(maxsize=1)
def _english_index() -> dict[str, int]:
    index: dict[str, int] = {}
    for surah in load_quran()["surahs"]:
        for key in _english_keys(surah["englishName"]):
            index.setdefault(key, surah["number"])
    return index


def find_surah(query: str) -> dict | None:
    """يبحث عن سورة برقمها أو باسمها العربي/الإنجليزي، أو None."""
    if not query:
        return None
    raw = str(query).strip()
    digits = raw.translate(_AR_DIGITS)
    if digits.isdigit():
        return _by_number(int(digits))

    # الاسم العربي (مع/بلا تشكيل، مع/بلا «سورة» و«ال» التعريف).
    arabic = normalize_arabic(raw)
    arabic = re.sub(r"^(سوره|سورة)\s+", "", arabic).strip()
    arabic = re.sub(r"^ال\s*", "", arabic).strip()
    if arabic:
        for surah in load_quran()["surahs"]:
            name = normalize_arabic(surah["name"])
            no_al = re.sub(r"^ال\s*", "", name).strip()
            if arabic in (name.strip(), no_al):
                return surah

    # الاسم الإنجليزي.
    for key in _english_keys(raw):
        hit = _english_index().get(key)
        if hit:
            return _by_number(hit)
    return None


def _require(surah: int, ayah: int) -> dict:
    entry = _by_number(int(surah))
    if entry is None:
        raise ValueError(f"رقم سورة خارج النطاق: {surah}")
    if not 1 <= int(ayah) <= len(entry["ayahs"]):
        raise ValueError(f"رقم آية خارج النطاق: {surah}:{ayah}")
    return entry


def global_ayah_number(surah: int, ayah: int) -> int:
    """الرقم العالمي للآية (ترتيبها في المصحف من 1 إلى 6236)."""
    entry = _require(surah, ayah)
    total = 0
    for s in load_quran()["surahs"]:
        if s["number"] == entry["number"]:
            break
        total += len(s["ayahs"])
    return total + int(ayah)


def get_ayah(surah: int, ayah: int) -> dict:
    entry = _require(surah, ayah)
    number = int(ayah)
    return {
        "surah": entry["number"],
        "surah_name": entry["name"],
        "ayah": number,
        "text": entry["ayahs"][number - 1],
        "global_number": global_ayah_number(surah, ayah),
    }


def ayah_audio_url(surah: int, ayah: int, reciter: str = "ar.alafasy") -> str:
    global_number = global_ayah_number(surah, ayah)
    return f"https://cdn.islamic.network/quran/audio/128/{reciter}/{global_number}.mp3"


def surah_audio_url(surah: int, reciter: str = "ar.alafasy") -> str:
    entry = _by_number(int(surah))
    if entry is None:
        raise ValueError(f"رقم سورة خارج النطاق: {surah}")
    return f"https://cdn.islamic.network/quran/audio-surah/128/{reciter}/{entry['number']}.mp3"


# ── التفسير (شبكة) ──────────────────────────────────────────────────
async def tafsir(surah: int, ayah: int, kind: str = "ar.muyassar") -> str:
    """يجلب تفسير آية من api.alquran.cloud؛ يرفع استثناءً عند الفشل."""
    _require(surah, ayah)
    payload = await http.get_json(f"{BASE}/ayah/{int(surah)}:{int(ayah)}/{kind}")
    data = payload.get("data") if isinstance(payload, dict) else None
    text = _clean((data or {}).get("text", ""))
    if not text:
        raise ValueError("لا يوجد تفسير متاح لهذه الآية")
    return text


async def ayah_details(surah: int, ayah: int) -> dict:
    """آية + تفسير ميسّر؛ فشل التفسير لا يُفشل الآية."""
    info = get_ayah(surah, ayah)
    try:
        info["tafsir"] = await tafsir(surah, ayah)
    except Exception:
        info["tafsir"] = ""
    return info


# ── البحث المحلي ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _index() -> list[tuple[int, int, str, str, str, int]]:
    """(رقم السورة، رقم الآية، النص، المطبّع، المتسامح، الرقم العالمي)."""
    rows: list[tuple[int, int, str, str, str, int]] = []
    global_number = 0
    for surah in load_quran()["surahs"]:
        for idx, text in enumerate(surah["ayahs"], start=1):
            global_number += 1
            normalized = normalize_arabic(text)
            rows.append(
                (surah["number"], idx, text, normalized, _loose(normalized), global_number)
            )
    return rows


def _query_terms(query: str) -> tuple[list[str], list[str]]:
    strict: list[str] = []
    loose: list[str] = []
    for word in normalize_arabic(query).split():
        if len(word) > 3 and word.startswith("ال"):
            word = word[2:]
        if word:
            strict.append(word)
            loose.append(_loose(word))
    return strict, loose


def search_ayahs(query: str, limit: int = 6) -> list[dict]:
    """بحث محلي في كل الآيات: كل كلمات الاستعلام يجب أن توجد."""
    if not query or len(normalize_arabic(query)) < 2:
        return []
    strict, loose = _query_terms(query)
    if not strict:
        return []

    surah_names = {s["number"]: s["name"] for s in load_quran()["surahs"]}
    rows = _index()
    results: list[dict] = []
    seen: set[int] = set()

    # مرور دقيق أولاً، ثم مرور متسامح إن لم نكتمل.
    for number, ayah, text, normalized, _loose_text, global_number in rows:
        if all(term in normalized for term in strict):
            seen.add(global_number)
            results.append(
                {
                    "surah": number,
                    "surah_name": surah_names[number],
                    "ayah": ayah,
                    "text": text,
                    "global_number": global_number,
                }
            )
            if len(results) >= limit:
                return results

    if len(results) < limit and all(len(t) >= 2 for t in loose):
        for number, ayah, text, _normalized, loose_text, global_number in rows:
            if global_number in seen:
                continue
            if all(term in loose_text for term in loose):
                seen.add(global_number)
                results.append(
                    {
                        "surah": number,
                        "surah_name": surah_names[number],
                        "ayah": ayah,
                        "text": text,
                        "global_number": global_number,
                    }
                )
                if len(results) >= limit:
                    break
    return results


# ── آية اليوم ───────────────────────────────────────────────────────
VERSE_OF_THE_DAY: list[tuple[int, int]] = [
    (2, 255),   # آية الكرسي
    (2, 286),
    (3, 139),
    (13, 28),
    (65, 3),
    (94, 5),
    (94, 6),
    (2, 186),
    (39, 53),
    (3, 159),
    (8, 46),
    (16, 128),
    (29, 69),
    (40, 60),
    (21, 87),
    (7, 205),
    (17, 78),
    (24, 35),
    (25, 63),
]


def verse_of_the_day() -> dict:
    """آية مختارة ثابتة في اليوم ذاته ومتغيّرة يومياً."""
    today = date.today()
    digest = hashlib.sha256(today.isoformat().encode("utf-8")).hexdigest()
    surah, ayah = VERSE_OF_THE_DAY[int(digest, 16) % len(VERSE_OF_THE_DAY)]
    return get_ayah(surah, ayah)


def random_ayah(bias: list[tuple[int, int]] | None = None) -> dict:
    """آية عشوائية من قائمة مختارة (زرّ «آية أخرى»)."""
    import random

    pool = bias or VERSE_OF_THE_DAY
    return get_ayah(*random.choice(pool))
