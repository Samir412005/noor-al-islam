"""اختبار دخاني شامل: يمرّر رسائل حقيقية عبر مسار المعالجة الكامل (بلا تيليجرام).

الاستعمال:  python scripts/smoke.py            (يستعمل الشبكة للمواقيت والتفسير)
            python scripts/smoke.py --offline  (يختبر فقط ما لا يحتاج شبكة)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import db  # noqa: E402
from app.handlers.core import process_text  # noqa: E402
from app.models import UserProfile  # noqa: E402

CASES: list[tuple[str, str]] = [
    ("آية اليوم", "quran"),
    ("2:255", "quran"),
    ("تفسير 2:255", "quran"),
    ("تلاوة الفاتحة", "quran"),
    ("سورة الملك", "quran"),
    ("ابحث عن الصبر", "quran"),
    ("حديث اليوم", "hadith"),
    ("البخاري 1", "hadith"),
    ("أحاديث عن بر الوالدين", "hadith"),
    ("أذكار الصباح", "adhkar"),
    ("دعاء الهم والحزن", "adhkar"),
    ("مواقيت الصلاة", "prayer"),
    ("القبلة", "prayer"),
    ("التاريخ الهجري", "prayer"),
    ("كم بقي للفجر", "prayer"),
    ("المناسبات القادمة", "occasions"),
    ("متى عيد الأضحى", "occasions"),
    ("ما معنى الصبر في القرآن؟", "quran"),
    # حواجز الحماية
    ("ما حكم بيع الذهب بالتقسيط؟", "guardrails"),
    ("أريد أن أنهي حياتي", "guardrails"),
    ("مرحبا", "scholar"),
    ("السلام عليكم", "scholar"),
    ("آية الكرسي", "quran"),
]


def preview(text: str, limit: int = 110) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


async def main() -> int:
    offline = "--offline" in sys.argv
    if offline:
        from app import guardrails
        from app.agents.scholar import ScholarAgent
        from app.services import hadith_api, quran_api
        from app.agents import adhkar as adhkar_agent

        quran_api.http.get_json = _offline_fail  # type: ignore[assignment]
        hadith_api.http.get_json = _offline_fail  # type: ignore[assignment]
        hadith_api.search_hadith = _offline_fail  # type: ignore[assignment]
        adhkar_agent.search_adhkar = lambda *a, **k: []  # type: ignore[assignment]
        ScholarAgent._offline_answer.__doc__ = "offline"
        _ = guardrails

    await db.connect()
    user = await db.get_user(424242, "اختبار")

    failures = 0
    for text, expected in CASES:
        try:
            reply = await process_text(text, user.chat_id, user)
        except Exception as exc:  # pragma: no cover
            print(f"❌ {text!r} → استثناء {type(exc).__name__}: {exc}")
            failures += 1
            continue

        got = reply.agent
        ok = expected in {"*", got}
        mark = "✅" if ok else "⚠️"
        if not ok:
            failures += 1
        print(f"{mark} {text!r:36} → {got:10} | أزرار {len(reply.buttons)} | صوت {'نعم' if reply.audio_url else 'لا'}")
        print(f"      {preview(reply.text)}")
        if reply.sources:
            print(f"      مصادر: {', '.join(reply.sources[:2])}")

    print("\n" + "─" * 70)
    print(f"النتيجة: {len(CASES) - failures}/{len(CASES)} حالة صحيحة")
    await db.close()
    return 1 if failures else 0


async def _offline_fail(*args, **kwargs):
    raise RuntimeError("الشبكة معطّلة في اختبار --offline")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
