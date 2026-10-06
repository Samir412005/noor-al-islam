#!/usr/bin/env python3
"""تنزيل أذكار «حصن المسلم» وتحويلها إلى ``app/data/adhkar.json``.

المصدر: https://cdn.jsdelivr.net/gh/rn0x/Adhkar-json@main/adhkar.json
البنية الواردة: قائمة أبواب، لكل باب ``category`` و``audio`` و``array`` من العناصر.

المخرَج:
    {
      "source": "...",
      "audio_base": "https://cdn.jsdelivr.net/gh/rn0x/Adhkar-json@main",
      "categories": [
        {"id": 1, "title": "...", "audio": "<url or ''>", "tags": [...],
         "items": [{"id": 1, "text": "...", "count": 1, "audio": "<url or ''>"}]}
      ]
    }

يُشغَّل مرة عند الإعداد (أو عند تحديث المصدر):
    python scripts/fetch_adhkar.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.text import clean, normalize_arabic  # noqa: E402

SOURCE_URL = "https://cdn.jsdelivr.net/gh/rn0x/Adhkar-json@main/adhkar.json"
AUDIO_BASE = "https://cdn.jsdelivr.net/gh/rn0x/Adhkar-json@main"
SOURCE_LABEL = "حصن المسلم — سعيد بن علي بن وهف القحطاني (متن)، عبر rn0x/Adhkar-json"
OUT_PATH = ROOT / "app" / "data" / "adhkar.json"

# ── قواعد الوسوم (tags) على عنوان الباب ─────────────────────────────
# المفاتيح مُطبَّعة مسبقاً (normalize_arabic): أ/إ/آ→ا، ة→ه، ى→ي، بلا تشكيل.
TAG_RULES: dict[str, list[str]] = {
    "صباح": ["صباح"],
    "مساء": ["مساء"],
    "نوم": ["نوم", "تقلب ليلا", "رؤيا", "الحلم", "الفزع", "الوحشه"],
    "استيقاظ": ["استيقاظ"],
    "وضوء": ["وضوء"],
    "منزل": ["منزل", "الخلاء", "دخول المنزل", "الخروج من المنزل"],
    "مسجد": ["مسجد"],
    "صلاة": [
        "صلاه", "الاذان", "الاستفتاح", "الركوع", "السجود", "الجلسه",
        "التشهد", "السلام", "الاستخاره", "الوتر", "قنوت", "سجود التلاوه",
        "الصفا", "المروه", "عرفه", "المشعر", "الجمار", "الركن",
    ],
    "هم": ["هم", "حزن", "كرب", "غم", "الضيق", "الوسوسه", "استصعب"],
    "مرض": ["مريض", "مرض", "عياده", "المحتضر", "الميت", "موت", "القبر", "مصيبه", "تعزيه"],
    "سفر": ["سفر", "المسافر", "المقيم للمسافر", "الركوب", "المركوب", "القرية", "البلده", "السوق", "الحج", "العمره"],
    "طعام": ["طعام", "افطار", "الصائم", "الضيف", "الشراب"],
    "لباس": ["لبس", "الثوب", "ثوبا"],
    "مطر": ["مطر", "الريح", "الرعد", "الاستسقاء", "الاستصحاء", "الهلال"],
    "تسبيح": ["تسبيح", "التحميد", "التهليل", "التكبير", "الاستغفار", "التوبه", "يسبح"],
    "رقية": ["رقيه", "يعوذ", "العين", "الحسد", "الشيطان", "الشياطين", "الدجال", "الشرك", "الطيره", "وجعا", "سحر"],
}
# الشكل مُطبَّعٌ مسبقاً حتى لا يُعيد normalize_arabic كل مرّة.
_COMPILED_TAGS = {
    tag: [normalize_arabic(k) for k in keys] for tag, keys in TAG_RULES.items()
}


def extract_tags(title: str) -> list[str]:
    """يستخرج وسوم الباب من عنوانه بقواعد الكلمات."""
    normalized = normalize_arabic(title)
    tags = [
        tag
        for tag, keywords in _COMPILED_TAGS.items()
        if any(kw in normalized for kw in keywords)
    ]
    return tags or ["general"]


def _absolute_audio(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"{AUDIO_BASE}/{value.lstrip('/')}"


def transform(raw: list[dict]) -> dict:
    """يحوّل بيانات المصدر الخام إلى الشكل الداخلي."""
    categories: list[dict] = []
    for entry in raw:
        title = clean(entry.get("category") or "")
        if not title:
            continue
        items: list[dict] = []
        for raw_item in entry.get("array") or []:
            text = clean(raw_item.get("text") or "")
            if not text:
                continue
            items.append(
                {
                    "id": int(raw_item.get("id") or len(items) + 1),
                    "text": text,
                    "count": int(raw_item.get("count") or 1),
                    "audio": _absolute_audio(raw_item.get("audio")),
                }
            )
        if not items:
            continue
        categories.append(
            {
                "id": int(entry.get("id") or len(categories) + 1),
                "title": title,
                "audio": _absolute_audio(entry.get("audio")),
                "tags": extract_tags(title),
                "items": items,
            }
        )
    return {
        "source": SOURCE_LABEL,
        "audio_base": AUDIO_BASE,
        "categories": categories,
    }


def fetch() -> list[dict]:
    response = httpx.get(SOURCE_URL, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def main() -> int:
    raw = fetch()
    data = transform(raw)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    categories = data["categories"]
    items = sum(len(c["items"]) for c in categories)
    tagged = sum(1 for c in categories if c["tags"] != ["general"])
    print(f"✅ كُتب {OUT_PATH.relative_to(ROOT)}")
    print(f"الأبواب: {len(categories)}")
    print(f"العناصر: {items}")
    print(f"أبواب موسومة: {tagged} — بلا وسم (general): {len(categories) - tagged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
