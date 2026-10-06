"""أدوات نصية: تهيئة رسائل تيليجرام، تقطيع، تطبيع عربي، أرقام."""
from __future__ import annotations

import html
import re
import unicodedata

TELEGRAM_LIMIT = 4096

# ── تطبيع عربي للبحث ────────────────────────────────────────────────
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u08F0-\u08FF]")
_TATWEEL = "\u0640"

_ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def strip_diacritics(text: str) -> str:
    """يحذف التشكيل والتطويل — يُستخدم في المقارنة لا في العرض."""
    return _DIACRITICS.sub("", text).replace(_TATWEEL, "")


def normalize_arabic(text: str) -> str:
    """تطبيع للبحث: توحيد الألف والهمزة والياء والتاء المربوطة."""
    text = unicodedata.normalize("NFKC", text)
    text = strip_diacritics(text)
    text = (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ٱ", "ا")
        .replace("ى", "ي")
        .replace("ئ", "ي")
        .replace("ؤ", "و")
        .replace("ة", "ه")
    )
    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def to_arabic_digits(text: str) -> str:
    return str(text).translate(_ARABIC_DIGITS)


def esc(text: str) -> str:
    """تهيئة للنشر بـ parse_mode=HTML."""
    return html.escape(str(text), quote=False)


class _SafeDict(dict):
    def __missing__(self, key):  # pragma: no cover - حماية من مفاتيح ناقصة
        return "{" + key + "}"


def render(template: str, **kwargs) -> str:
    """قوالب آمنة: {name} لا يرفع استثناء عند غياب المفتاح."""
    return template.format_map(_SafeDict(**kwargs))


def chunk(text: str, limit: int = TELEGRAM_LIMIT, *, reserve: int = 0) -> list[str]:
    """يقطّع نصاً طويلاً إلى مقاطع ≤ limit مع احترام حدود الأسطر."""
    limit = max(200, limit - reserve)
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        if len(line) <= limit:
            current = line
            continue
        # سطر واحد أطول من الحد ⇒ تقطيع قسري
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            parts.append(line[:cut])
            line = line[cut:].lstrip()
        current = line
    if current:
        parts.append(current)
    return [p for p in parts if p.strip()] or [""]


def bullet_list(items: list[str], bullet: str = "•") -> str:
    return "\n".join(f"{bullet} {i}" for i in items)


def clean(text: str) -> str:
    """تنظيف عام: مسافات زائدة وأسطر فارغة متكررة."""
    text = re.sub(r"[ \t]+", " ", str(text or ""))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
