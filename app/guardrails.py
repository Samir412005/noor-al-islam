"""حواجز الحماية (Guardrails) — قلبُ مصداقية البوت.

المبدأ: البوت **مرآةٌ للنصوص لا مُفتٍ**. لذلك:
1. لا يُصدر أحكاماً شرعية (فتوى) — يُحيل إلى أهل العلم.
2. لا يعرض حديثاً أو آية بلا مرجع صريح.
3. يكشف محاولات الانتحال (نصّ يُنسب للنبي ﷺ دون تخريج) ويُنبّه.
4. يتعامل بحذر مع الأسئلة الحسّاسة (تكفير، طائفية، عنف، أذى نفس).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .text import normalize_arabic

# ── تصنيف الأسئلة الحسّاسة ────────────────────────────────────────
FATWA_PATTERNS = [
    r"\bحكم\b", r"\bحُكم\b", r"هل يجوز", r"يجوز لي", r"ما حكم", r"حرام", r"حلال",
    r"\bفتوى\b", r"أفتوني", r"افتوني", r"\bطلاق\b", r"\bميراث\b", r"\bزكاة\b.*\bكم\b",
    r"\bتكفير\b", r"هل هو كافر", r"\bبدعة\b", r"\bرضاع\b", r"\bعدة\b", r"\bنكاح\b",
    r"\bيمين\b.*\bكفارة\b", r"كفارة", r"\bتوبة\b.*\bكيف\b",
]

# ملاحظة: تُكتب الأنماط بصيغة **مُطبَّعة** (بلا همزات/تشكيل) لأن الفحص يمرّ على
# normalize_arabic — هكذا لا تُفلت صيغة مثل «أنهي حياتي» مقابل «انهي حياتي».
SENSITIVE_PATTERNS = {
    "self_harm": [
        r"انتحار", r"انتحر", r"انهي حياتي", r"اقتل نفسي", r"اقتل نفسى",
        r"اذي نفسي", r"اؤذي نفسي", r"اؤذى نفسى", r"اضرب نفسي",
        r"اتمني الموت", r"اتمنى الموت", r"اريد الموت", r"افكر في الموت",
        r"ما عاد لي رغبه في الحياه", r"اكره حياتي", r"تعبت من الحياه",
        r"لا اريد ان اعيش", r"خلصني من حياتي",
    ],
    "sihr_general": [r"\bسحر\b", r"سحر", r"حسد", r"رقيه", r"رقية", r"شياطين"],
    "sectarian": [r"تكفيري", r"خوارج", r"رافضه", r"نواصب", r"شيعه.*سنه", r"سنه.*شيعه"],
}

# كلمات تُطابَق كوحدة كاملة (لا داخل كلمة أطول) — أدقّ للكلمات القصيرة
SENSITIVE_WORDS: dict[str, set[str]] = {
    "sihr_general": {
        normalize_arabic(w)
        for w in ("مس", "تجني", "الجن", "جن", "ساحر", "مشعوذ", "وسواس")
    },
}

# إشارات قويّة للفتوى — تُوقف الطلب قبل أن يصل أيّ وكيل.
STRONG_FATWA_PATTERNS = [
    r"هل يجوز", r"هل يحل", r"ما حكم", r"ما هو حكم", r"حكم الشرع", r"ما قول الشرع",
    r"يجوز لي", r"هل يجوز لي", r"افتوني", r"افتني", r"افتى", r"فتوى", r"\bفتوي\b",
    r"حلال ام حرام", r"حرام ام حلال", r"هل هو حرام", r"هل هو حلال",
    r"كفاره يمين", r"هل طلاقي", r"هل بطلت صلاتي",
]

CRISIS_REPLY = (
    "أخي/أختي، ما تشعر به ثقيلٌ جداً، ولا أريدك أن تحمله وحدك.\n\n"
    "🔴 إن كنت تفكّر في إيذاء نفسك، تحدّث الآن مع شخص تثق به، واتصل بخدمة الطوارئ في بلدك "
    "(في الجزائر: الحماية المدنية 14 أو 1021).\n\n"
    "وأنت في قلبي: ﴿وَلَا تَقْتُلُوا أَنفُسَكُمْ إِنَّ اللَّهَ كَانَ بِكُمْ رَحِيمًا﴾ [النساء: 29]، "
    "وقوله ﷺ: «لا يُلْقِ أحدُكم نفسَه في التهلكة» — والله لا يُضيع من لجأ إليه.\n\n"
    "أنا هنا لأسمعك، وأذكّرك بالله ما دمت تحتاج."
)

FATWA_REPLY = (
    "أحسنت السؤال، وهو من باب <b>الفتوى</b>، ولستُ أهلاً لها — وأنا ذكاءٌ آليّ لا يملك "
    "أن يُحلّل أو يُحرّم.\n\n"
    "ما أستطيع تقديمه:\n"
    "• النصوص الشرعية المتّصلة بموضوعك (آيات وأحاديث) بتخريجها.\n"
    "• كلام أهل العلم المنشور في المصادر المعتمدة، بمن يوسم بالمرجع.\n\n"
    "وللفتوى المعتبرة، اسأل دار الإفتاء في بلدك أو عالماً موثوقاً. "
    "أكتب سؤالك بصيغة «اذكر لي الآيات والأحاديث في …» وسأُحضِر لك الأصول."
)

SIHR_REPLY = (
    "أمورُ الغيب بابها ضيّق، والتوسّع فيها مظنّة الوسواس.\n\n"
    "ما صحّ فيها ثابت بالتوكّل والرقية المشروعة: قراءة الفاتحة، وآية الكرسي، "
    "والمعوّذتين، مع ﴿وَمَا هُم بِضَارِّينَ بِهِ مِنْ أَحَدٍ إِلَّا بِإِذْنِ اللَّهِ﴾ [البقرة: 102].\n\n"
    "واحذر من مشعوذي «فك السحر» وكشف الغيب — فالاتجار بهذا الباب من أكبر أذى الناس. "
    "وإن اشتدّ الأمر فمرجعك طبيبٌ مختصّ وعالِمٌ موثوق."
)

SECTARIAN_REPLY = (
    "أعتذر: لا أُنشغل بالنياشين الطائفية ولا بتزكية أحدٍ ولا تكفيره.\n\n"
    "أنا هنا لنفعٍ عمليّ: آيةٌ تقرؤها، حديثٌ تتعلّمه، ذكرٌ يُطمئن قلبك، "
    "ومواقيتُ صلاتك. إن كان سؤالك فقهياً عاماً فأحضِر له الأصل من الكتاب والسنة، "
    "وإن كان خلافيّاً فمرجعه أهل العلم المنصفون."
)

# ── كشف الانتحال (حديث بلا تخريج) ─────────────────────────────────
_HADITH_CLAIM = re.compile(
    r"(قال\s*(?:رسولُ?\s*الله|النبيُ?|النبيّ)\s*صلى\s*الله\s*عليه\s*وسلم|"
    r"قال\s*رسول\s*الله|عن\s*النبي\s*ﷺ|قال\s*ﷺ|«.*».*رسول\s*الله)",
    re.UNICODE,
)
SOURCE_MARKERS = re.compile(
    r"(رواه|أخرجه|البخاري|مسلم|أبو\s*داود|الترمذي|النسائي|ابن\s*ماجه|"
    r"أحمد|مالك|موطأ|الدارمي|صحيح|سنن|\bمسند\b|رقم|حديث\s*رقم|sunnah\.com)",
    re.UNICODE,
)
_AYAH_CLAIM = re.compile(r"(قال\s*الله\s*تعالى|﴿)", re.UNICODE)
_SURAH_MARK = re.compile(r"\[[^\]]{2,40}:\s*\d+\]|\(\s*\d+\s*:\s*\d+\s*\)")


def detect_fatwa(text: str) -> bool:
    """كشف عامّ لأسئلة الأحكام (يُستعمل داخل وكيل العلم)."""
    if not text:
        return False
    normalized = normalize_arabic(text)
    return any(
        re.search(pattern, candidate, re.UNICODE)
        for pattern in FATWA_PATTERNS
        for candidate in (text, normalized)
    )


def detect_strong_fatwa(text: str) -> bool:
    """إشارات قويّة فقط — تُستعمل لحجب الطلب قبل التوجيه لأي وكيل."""
    if not text:
        return False
    normalized = normalize_arabic(text)
    return any(
        re.search(pattern, normalized, re.UNICODE) for pattern in STRONG_FATWA_PATTERNS
    )


def detect_sensitive(text: str) -> str | None:
    if not text:
        return None
    normalized = normalize_arabic(text)
    tokens = _token_set(normalized)
    for category, patterns in SENSITIVE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, normalized, re.UNICODE):
                return category
    for category, words in SENSITIVE_WORDS.items():
        if tokens & words:
            return category
    return None


def _token_set(normalized: str) -> set[str]:
    """كلمات النصّ مع صيغها الشائعة (بلا «ال» و«و» الابتدائيتين).

    مطابقة الكلمات كاملة تمنع أخطاء فادحة مثل اعتبار «الجنة» من «الجن».
    """
    result: set[str] = set()
    for token in normalized.split():
        candidates = {token}
        if token.startswith("ال") and len(token) > 3:
            candidates.add(token[2:])
        if token.startswith("و") and len(token) > 3:
            candidates.add(token[1:])
        for candidate in candidates:
            if candidate:
                result.add(candidate)
    return result


def guard_text(category: str) -> str | None:
    return {
        "self_harm": CRISIS_REPLY,
        "sihr_general": SIHR_REPLY,
        "sectarian": SECTARIAN_REPLY,
    }.get(category)


def unsourced_attributions(text: str) -> bool:
    """هل يحتوي النص على نصوص منسوبة بلا مرجع؟"""
    if not text:
        return False
    if SOURCE_MARKERS.search(text) or _SURAH_MARK.search(text):
        return False
    return bool(_HADITH_CLAIM.search(text) or _AYAH_CLAIM.search(text))


DISCLAIMER = (
    "\n\n— — —\n"
    "<i>⚠️ لم أجد مرجعاً مرفقاً لهذا النص. المرجوّ التحقّق من تخريجه في المصادر "
    "قبل الأخذ به أو نشره، فالنقول عن النبي ﷺ لا تُقبل إلا بثبوتها.</i>"
)

SCHOLAR_FOOTER = (
    "\n\n— — —\n"
    "<i>مصدرٌ ونقل، لا فتوى. وللحكم الشرعي اسأل دار الإفتاء أو عالماً موثوقاً.</i>"
)


def apply_output_guard(text: str, *, had_sources: bool = False) -> tuple[str, list[str]]:
    """يفحص ردّ الـLLM ويعيد (النص المُصحَّح، التحذيرات)."""
    warnings: list[str] = []
    if had_sources:
        return text, warnings
    if unsourced_attributions(text):
        warnings.append("unsourced_attribution")
        text = text.rstrip() + DISCLAIMER
    return text, warnings


@dataclass(frozen=True)
class GuardDecision:
    blocked: bool
    category: str | None = None
    response: str | None = None


def precheck(text: str) -> GuardDecision:
    """فحص قبل التوجيه لأي وكيل: الأمن النفسي أولاً، ثم الفتوى."""
    sensitive = detect_sensitive(text)
    # سؤال معرفي عن الغيبيّات (تفسير/سورة/قصة) ليس حالة رقية — لا نحجبه
    if sensitive == "sihr_general" and _is_study_question(text):
        sensitive = None
    if sensitive:
        reply = guard_text(sensitive)
        if reply:
            return GuardDecision(blocked=True, category=sensitive, response=reply)

    if detect_strong_fatwa(text):
        return GuardDecision(blocked=True, category="fatwa", response=FATWA_REPLY)

    return GuardDecision(blocked=False)


_STUDY_HINTS = re.compile(
    r"(في القران|القران الكريم|تفسير|سوره|ايه|اسباب النزول|قصه|موضوع|تلاوه)",
    re.UNICODE,
)


def _is_study_question(text: str) -> bool:
    """سؤال دراسة/معرفة لا شكوى حسّاسة."""
    return bool(_STUDY_HINTS.search(normalize_arabic(text or "")))


# ── مطابقة الشواهد بمخرجات الأدوات (منع الهلوسة) ────────────────────
_QUOTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"﴿[^﴾]{8,}﴾"),
    re.compile(r"«[^»]{8,}»"),
    re.compile(r"\"[^\"]{8,}\"", re.UNICODE),
)
_CLAIM_MARKER = re.compile(
    r"(قال\s*(?:رسول\s*الله|النبي|ﷺ)|عن\s*النبي|عن\s*أبي\s*هريرة|"
    r"رواه|أخرجه|اخرجه|حديث\s*رقم|روى\s|﴿|﴾)",
    re.UNICODE,
)


def _has_corpus_match(fragment: str, normalized_corpus: str, window: int = 16) -> bool:
    normalized = normalize_arabic(fragment)
    if len(normalized) < window:
        return normalized in normalized_corpus if normalized else False
    step = max(4, window // 4)
    for start in range(0, len(normalized) - window + 1, step):
        if normalized[start : start + window] in normalized_corpus:
            return True
    return normalized[:window] in normalized_corpus


def verify_quotes(text: str, corpus: str, *, window: int = 16) -> tuple[str, list[str]]:
    """يحذف كل سطر فيه نصٌّ منسوب لا يطابق ما أعادته الأدوات حرفياً.

    هذه هي الحصانة الحقيقية ضد هلوسة النصوص الشرعية: الكلام العامّ يمرّ،
    أمّا ما يُنسب إلى الوحي فلا بدّ أن يكون في المصادر المستدعاة.
    """
    if not text:
        return text, []
    normalized_corpus = normalize_arabic(corpus or "")
    kept: list[str] = []
    dropped = 0

    for line in text.split("\n"):
        if not _CLAIM_MARKER.search(line):
            kept.append(line)
            continue
        anchors: list[str] = []
        for pattern in _QUOTE_PATTERNS:
            anchors.extend(pattern.findall(line))
        if not anchors:
            anchors = [line]
        if any(_has_corpus_match(anchor, normalized_corpus, window=window) for anchor in anchors):
            kept.append(line)
        else:
            dropped += 1

    warnings: list[str] = []
    result = "\n".join(kept).strip()
    if dropped:
        warnings.append("unverified_quotes")
        result = (
            result
            + "\n\n<i>⚠️ حُذف من الجواب نصٌّ منسوب لم أستطع مطابقته بمصادر الأدوات، "
            "وذلك صيانةً للوحي من الخطأ. اسأل عن مصدره صراحةً لأعرضه بالتفصيل.</i>"
        )
    return result, warnings
