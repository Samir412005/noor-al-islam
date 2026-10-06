#!/usr/bin/env python3
"""يجلب نصّ القرآن كاملاً (رواية حفص / quran-uthmani) ويكتبه في ملف JSON مضغوط.

المصدر: https://api.alquran.cloud/v1/quran/quran-uthmani
الناتج: app/data/quran_uthmani.json

    {
      "source": "...",
      "surahs": [
        {"number": 1, "name": "الفاتحة", "englishName": "Al-Fatiha",
         "revelationType": "Meccan", "ayahs": ["نص الآية 1", ...]},
        ...
      ]
    }

التشغيل:
    python scripts/fetch_quran.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:  # httpx مثبّت في المشروع، لكن نتيح البديل القياسي عند الحاجة
    import httpx
except Exception:  # pragma: no cover - بيئات بلا httpx
    httpx = None  # type: ignore[assignment]

API_URL = "https://api.alquran.cloud/v1/quran/quran-uthmani"
SOURCE = "api.alquran.cloud — quran-uthmani (رواية حفص)"
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "app" / "data" / "quran_uthmani.json"


def _clean(text: str) -> str:
    """يزيل BOM والمحارف غير المرئية ويرتّب المسافات."""
    text = str(text or "").replace("\ufeff", "").replace("\u200b", "")
    return " ".join(text.split())


_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u08F0-\u08FF]")


def _surah_name(text: str) -> str:
    """ينزع السابقة «سُورَةُ» والتشكيل ليصبح الاسم «الفاتحة» وهكذا."""
    name = _clean(text)
    for prefix in ("سُورَةُ ", "سورة ", "سُورَة "):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    name = _DIACRITICS.sub("", name).replace("\u0640", "").replace("\u0671", "\u0627")
    return " ".join(name.split())


def fetch_payload() -> dict:
    """يجلب الاستجابة الخام من الشبكة (dict)."""
    if httpx is not None:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(API_URL)
            response.raise_for_status()
            return response.json()

    from urllib.request import urlopen  # pragma: no cover - بديل احتياطي

    with urlopen(API_URL, timeout=60) as handle:  # noqa: S310 - عنوان ثابت
        return json.loads(handle.read().decode("utf-8"))


def normalize_quran(raw: dict) -> dict:
    """يحوّل استجابة الـAPI إلى الشكل المطلوب محلياً."""
    data = raw.get("data") or {}
    surahs_out: list[dict] = []
    for surah in data.get("surahs", []):
        ayahs = [
            _clean(ayah.get("text", ""))
            for ayah in sorted(surah.get("ayahs", []), key=lambda a: a.get("numberInSurah", 0))
        ]
        surahs_out.append(
            {
                "number": int(surah.get("number", len(surahs_out) + 1)),
                "name": _surah_name(surah.get("name", "")),
                "englishName": _clean(surah.get("englishName", "")),
                "revelationType": (surah.get("revelationType") or "").strip(),
                "ayahs": ayahs,
            }
        )
    return {"source": SOURCE, "surahs": surahs_out}


def save(payload: dict) -> Path:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return DATA_FILE


def main() -> int:
    print(f"→ يجلب: {API_URL}")
    raw = fetch_payload()
    payload = normalize_quran(raw)

    surahs = payload["surahs"]
    ayah_count = sum(len(s["ayahs"]) for s in surahs)
    if len(surahs) != 114 or ayah_count == 0:
        print(f"✗ بيانات غير مكتملة: {len(surahs)} سورة / {ayah_count} آية", file=sys.stderr)
        return 1

    path = save(payload)
    size = path.stat().st_size
    print(f"✓ المصدر: {payload['source']}")
    print(f"✓ عدد السور: {len(surahs)}")
    print(f"✓ عدد الآيات: {ayah_count}")
    print(f"✓ الملف: {path}  ({size / 1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
