"""وكيل القرآن — آيات، تفسير، تلاوة، وبحث.

لا يختلق نصاً شرعياً أبداً: كل آية وتفسير من `quran_api` (ملف محلي أو الشبكة).
كل الردود HTML، وكل نص ديناميكي يمرّ عبر `esc`.
"""
from __future__ import annotations

import logging
import re

from ..models import AgentReply, AgentRequest, Button
from ..services import quran_api
from ..text import esc, normalize_arabic, to_arabic_digits
from .base import BaseAgent, extract_arabic_ayah_reference

log = logging.getLogger(__name__)

PLACE_AR = {"Meccan": "مكية", "Medinan": "مدنية"}
STOPWORDS = {
    "سوره", "سورة", "ايه", "آية", "اية", "من", "في", "عن", "علي", "على", "الى", "إلى",
    "رقم", "القران", "القرآن", "قران", "قرآن", "تفسير", "تلاوه", "تلاوة", "استمع", "اسمع",
    "بحث", "ابحث", "اريد", "أريد", "اعرض", "أعرض", "لي", "يا", "هو", "هي", "ثم", "ال",
}
NO_NETWORK = "تعذّر جلب التفسير الآن — تحقّق من اتصالك بالإنترنت ثم أعِد المحاولة."
NO_NETWORK_SEARCH = "تعذّر جلب البيانات الآن — تحقّق من اتصالك ثم أعِد المحاولة."
_SEARCH_PREFIX = re.compile(r"^\s*(?:ابحث|بحث)\s*(?:في\s+القرآن(?:\s+الكريم)?)?\s*(?:عن\s+)?")
# أدوات استفهام ومقدمات تُنزع من السؤال قبل البحث
_Q_LEAD = re.compile(
    r"^\s*(?:ما|ماذا|من|كيف|لماذا|هل|معنى|معني|أخبرني|اخبرني|اذكر|أريد|اريد|"
    r"ابحث|بحث|عن|لي|أعطني|اعطني|وضح|أشرح|اشرح|فسّر|فسر|في)\s+"
)
_Q_TAIL = re.compile(
    r"\s*(?:في\s+القرآن(?:\s+الكريم)?|من\s+القرآن|بالقرآن|في\s+كتاب\s+الله)\s*$"
)

# ألقاب مشهورة لآيات ⇒ مرجع مباشر
_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:آية|ايه|اية)?\s*الكرسي", re.UNICODE), "2:255"),
)


def _expand_aliases(text: str) -> str:
    for pattern, replacement in _ALIASES:
        text = pattern.sub(replacement, text)
    return text


def _clean_query(text: str) -> str:
    """ينزع مقدمات السؤال («ما معنى… في القرآن؟») ليبقى مصطلح البحث وحده."""
    cleaned = _SEARCH_PREFIX.sub("", (text or "").strip()).strip(" ؟?!.،:؛")
    for _ in range(5):
        nxt = _Q_LEAD.sub("", cleaned).strip()
        if nxt == cleaned:
            break
        cleaned = nxt
    cleaned = _Q_TAIL.sub("", cleaned).strip().strip(" ؟?!.،:؛")
    if len(normalize_arabic(cleaned)) < 2:
        tokens = [
            t for t in normalize_arabic(text or "").split() if t not in STOPWORDS and len(t) >= 3
        ]
        cleaned = " ".join(tokens) or cleaned
    return cleaned or (text or "").strip()


def _btn(text: str, action: str, *args: object) -> Button:
    data = ":".join(["ag", "quran", action, *[str(a) for a in args]])
    return Button(text=text, data=data)


def _ref(ayah: dict) -> str:
    return f"[{esc(ayah['surah_name'])}: {to_arabic_digits(ayah['ayah'])}]"


def _ayah_block(ayah: dict) -> str:
    return f"﴿ {esc(ayah['text'])} ﴾\n<i>{_ref(ayah)}</i>"


def _surah_name_in(text: str) -> dict | None:
    """يبحث عن اسم سورة داخل نصّ طويل («آية 255 من سورة البقرة»)."""
    direct = quran_api.find_surah(text)
    if direct:
        return direct
    normalized = normalize_arabic(text)
    normalized = re.sub(r"[0-9]+", " ", normalized)
    tokens = [t for t in normalized.split() if t not in STOPWORDS and len(t) >= 2]
    for i in range(len(tokens) - 1):
        hit = quran_api.find_surah(f"{tokens[i]} {tokens[i + 1]}")
        if hit:
            return hit
    for token in tokens:
        hit = quran_api.find_surah(token)
        if hit:
            return hit
    return None


class QuranAgent(BaseAgent):
    name = "quran"
    title = "وكيل القرآن"
    description = "آيات القرآن وتفسيرها وتلاوتها والبحث فيها — برواية حفص."
    priority = 80
    keywords = [
        "قرآن", "القرآن", "آية", "ايه", "سورة", "سوره", "تفسير", "تلاوة",
        "اقرأ", "اقرا", "آية اليوم", "احفظ", "بحث في القرآن",
        "ابحث", "ابحث عن", "الكرسي", "آية الكرسي",
    ]
    examples = [
        "آية اليوم",
        "سورة الملك",
        "البقرة 255",
        "تفسير 2:255",
        "تلاوة الفاتحة",
        "ابحث عن الصبر",
    ]

    async def handle(self, request: AgentRequest) -> AgentReply:
        text = (request.text or "").strip()
        args = (request.args or "").strip()
        if args and args not in text:
            text = f"{text} {args}".strip()
        try:
            return await self._dispatch(request, text)
        except Exception:  # pragma: no cover - حماية قصوى: لا traceback للمستخدم
            log.exception("quran agent failed")
            return self.reply(
                "حدث خطأ غير متوقّع أثناء معالجة طلبك. أعِد المحاولة بعد قليل.",
                agent=self.name,
                sources=[quran_api.SOURCE_QURAN],
            )

    # ── التوجيه ────────────────────────────────────────────────────
    async def _dispatch(self, request: AgentRequest, text: str) -> AgentReply:
        text = _expand_aliases(text)
        norm = normalize_arabic(text)

        if "ايه اليوم" in norm or "اية اليوم" in norm or "اية اليوم" in text:
            return self._verse_of_the_day()

        if "تفسير" in norm:
            return await self._tafsir(request, text)

        if "تلاوه" in norm or "تلاوة" in text or "استمع" in norm or "اسمع" in norm:
            return self._audio(text, request.user.reciter)

        reference = self._reference_of(text, norm)
        if reference is not None:
            surah_no, ayah_no = reference
            if surah_no == 0:
                # «سورة 67» بلا كلمة «آية» ⇐ الرقم هو رقم السورة
                if "سوره" in norm and "ايه" not in norm:
                    candidate = quran_api.find_surah(str(ayah_no))
                    if candidate is not None:
                        return self._surah_info(candidate["number"], request.user.reciter)
                surah = _surah_name_in(text)
                if surah is None:
                    return self._clarify()
                surah_no = surah["number"]
            return self._reference(surah_no, ayah_no)

        surah = _surah_name_in(text)
        if surah is not None:
            return self._surah_info(surah["number"], request.user.reciter)

        return self._search(text)

    # ── الوجهات ────────────────────────────────────────────────────
    @staticmethod
    def _reference_of(text: str, norm: str) -> tuple[int, int] | None:
        """يستخرج مرجع الآية، ويدعم «آية 255 من سورة البقرة» أيضاً."""
        reference = extract_arabic_ayah_reference(text)
        if reference is not None:
            return reference
        match = re.search(r"(?:ايه|الايه|اية)\s*(\d{1,3})", norm)
        if match:
            return 0, int(match.group(1))
        return None

    def _verse_of_the_day(self) -> AgentReply:
        ayah = quran_api.verse_of_the_day()
        body = _ayah_block(ayah)
        reply = self.reply(
            f"<b>🌿 آية اليوم</b>\n\n{body}",
            agent=self.name,
            sources=[quran_api.SOURCE_QURAN],
        )
        reply.buttons = [
            [
                _btn("📖 التفسير", "tafsir", ayah["surah"], ayah["ayah"]),
                _btn("🎧 تلاوة", "audio", ayah["surah"], ayah["ayah"]),
            ],
            [_btn("🔄 آية أخرى", "random")],
        ]
        return reply

    def _reference(self, surah_no: int, ayah_no: int) -> AgentReply:
        try:
            ayah = quran_api.get_ayah(surah_no, ayah_no)
        except ValueError:
            return self.reply(
                "لم أجد هذه الآية. تأكّد من رقم السورة (١–١١٤) ورقم الآية ضمن حدودها.",
                agent=self.name,
            )
        reply = self.reply(
            _ayah_block(ayah),
            agent=self.name,
            sources=[quran_api.SOURCE_QURAN],
        )
        reply.buttons = [
            [
                _btn("📖 التفسير", "tafsir", ayah["surah"], ayah["ayah"]),
                _btn("🎧 تلاوة", "audio", ayah["surah"], ayah["ayah"]),
            ],
            [_btn("🔄 آية أخرى", "random")],
        ]
        return reply

    async def _tafsir(self, request: AgentRequest, text: str) -> AgentReply:
        norm = normalize_arabic(text)
        reference = self._reference_of(text, norm)
        if reference is not None and reference[0] != 0:
            surah_no, ayah_no = reference
        else:
            ayah_no = reference[1] if reference is not None else 1
            surah = _surah_name_in(text)
            if surah is None:
                return self._clarify()
            surah_no = surah["number"]

        try:
            ayah = quran_api.get_ayah(surah_no, ayah_no)
        except ValueError:
            return self.reply(
                "لم أجد هذه الآية. تأكّد من رقم السورة والآية.",
                agent=self.name,
            )

        kind = "ar.muyassar"
        # allow a specific kind passed in text: e.g. "تفسير 2:255 ar.qurtubi"
        for candidate in quran_api.TAFSIR_KINDS:
            if candidate in text:
                kind = candidate
                break

        try:
            tafsir_text = await quran_api.tafsir(ayah["surah"], ayah["ayah"], kind)
        except Exception:
            return self.reply(
                f"<b>﴿ {esc(ayah['text'][:80])}{'…' if len(ayah['text']) > 80 else ''} ﴾</b>\n"
                f"<i>{_ref(ayah)}</i>\n\n{NO_NETWORK}",
                agent=self.name,
                sources=[quran_api.SOURCE_QURAN],
            )

        source = (
            quran_api.SOURCE_TAFSIR
            if kind == "ar.muyassar"
            else f"{quran_api.TAFSIR_KINDS[kind]} — api.alquran.cloud"
        )
        label = quran_api.TAFSIR_KINDS.get(kind, kind)
        reply = self.reply(
            f"<b>📖 {esc(label)}</b>\n\n"
            f"﴿ {esc(ayah['text'])} ﴾\n<i>{_ref(ayah)}</i>\n\n"
            f"{esc(tafsir_text)}",
            agent=self.name,
            sources=[quran_api.SOURCE_QURAN, source],
        )
        alternatives = [k for k in quran_api.TAFSIR_KINDS if k != kind][:2]
        rows = [[_btn(f"📚 {quran_api.TAFSIR_KINDS[k]}", "tafsirkind", k, ayah["surah"], ayah["ayah"]) for k in alternatives]]
        rows.append(
            [
                _btn("🎧 تلاوة", "audio", ayah["surah"], ayah["ayah"]),
                _btn("🔤 الآية", "surah", ayah["surah"]),
            ]
        )
        reply.buttons = rows
        return reply

    def _audio(self, text: str, reciter: str | None = None) -> AgentReply:
        norm = normalize_arabic(text)
        reference = self._reference_of(text, norm)
        if reference is not None:
            surah_no, ayah_no = reference
            if surah_no == 0:
                surah = _surah_name_in(text)
                if surah is None:
                    return self._clarify()
                surah_no = surah["number"]
            try:
                ayah = quran_api.get_ayah(surah_no, ayah_no)
                url = quran_api.ayah_audio_url(ayah["surah"], ayah["ayah"], reciter or "ar.alafasy")
            except ValueError:
                return self.reply("لم أجد هذه الآية للتلاوة.", agent=self.name)
            title = f"{ayah['surah_name']} — الآية {to_arabic_digits(ayah['ayah'])}"
            return self.reply(
                f"🎧 <b>تلاوة {esc(title)}</b>",
                agent=self.name,
                audio_url=url,
                audio_title=title,
                sources=[quran_api.SOURCE_QURAN],
                buttons=[[_btn("📖 التفسير", "tafsir", ayah["surah"], ayah["ayah"])]],
            )

        surah = _surah_name_in(text)
        if surah is None:
            return self._clarify()
        try:
            url = quran_api.surah_audio_url(surah["number"], reciter or "ar.alafasy")
        except ValueError:
            return self.reply("لم أجد هذه السورة للتلاوة.", agent=self.name)
        title = f"سورة {surah['name']} كاملة"
        return self.reply(
            f"🎧 <b>تلاوة {esc(title)}</b>",
            agent=self.name,
            audio_url=url,
            audio_title=title,
            sources=[quran_api.SOURCE_QURAN],
            buttons=[[_btn("📖 السورة", "surah", surah["number"])]],
        )

    def _surah_info(self, surah_no: int, reciter: str | None = None) -> AgentReply:
        surahs = quran_api.surah_list()
        entry = next((s for s in surahs if s["number"] == surah_no), None)
        if entry is None:
            return self.reply("لم أجد هذه السورة.", agent=self.name)

        raw = quran_api.load_quran()["surahs"][surah_no - 1]
        place = PLACE_AR.get(raw.get("revelationType", ""), "مكية")
        first = raw["ayahs"][:5]
        lines = [
            f"<b>📗 سورة {esc(entry['name'])}</b>",
            f"<i>{esc(entry['englishName'])} • {to_arabic_digits(entry['ayahs_count'])} آية • {place}</i>",
            "",
        ]
        for idx, verse in enumerate(first, start=1):
            lines.append(f"<b>{to_arabic_digits(idx)}.</b> ﴿ {esc(verse)} ﴾")
        if entry["ayahs_count"] > len(first):
            lines.append("\n<i>… وأكملها بالتلاوة أو بطلب الآية برقمها.</i>")

        rows: list[list[Button]] = [
            [
                _btn("🎧 تلاوة السورة كاملة", "audiosurah", surah_no),
                _btn("📖 تفسير آية ١", "tafsir", surah_no, 1),
            ]
        ]
        nav: list[Button] = []
        if surah_no > 1:
            nav.append(_btn("⬅️ السورة السابقة", "surah", surah_no - 1))
        if surah_no < len(surahs):
            nav.append(_btn("السورة التالية ➡️", "surah", surah_no + 1))
        if nav:
            rows.append(nav)
        return self.reply(
            "\n".join(lines),
            agent=self.name,
            sources=[quran_api.SOURCE_QURAN],
            buttons=rows,
        )

    def _search(self, text: str) -> AgentReply:
        query = _clean_query(text)
        if len(normalize_arabic(query)) < 2:
            return self.reply(
                "اكتب آيةً أو كلمةً للبحث في القرآن، أو اطلب «آية اليوم» أو «سورة الملك».",
                agent=self.name,
            )
        results = quran_api.search_ayahs(query, limit=6)
        if not results:
            return self.reply(
                f"لم أعثر على نتائج للبحث عن «{esc(query)}».\n"
                "جرّب كلمةً أقصر أو صيغةً أخرى.",
                agent=self.name,
                sources=[quran_api.SOURCE_QURAN],
            )
        blocks = [f"<b>🔎 نتائج البحث عن «{esc(query)}»</b>", ""]
        buttons: list[list[Button]] = []
        for ayah in results:
            blocks.append(_ayah_block(ayah))
            blocks.append("")
            buttons.append(
                [
                    _btn(f"📖 تفسير {_ref(ayah)}", "tafsir", ayah["surah"], ayah["ayah"]),
                    _btn("🎧 تلاوة", "audio", ayah["surah"], ayah["ayah"]),
                ]
            )
        return self.reply(
            "\n".join(blocks).strip(),
            agent=self.name,
            sources=[quran_api.SOURCE_QURAN],
            buttons=buttons,
        )

    def _clarify(self) -> AgentReply:
        return self.reply(
            "لم أفهم المرجع المطلوب. اذكر السورة ورقم الآية، مثل: «البقرة ٢٥٥» أو «2:255»،\n"
            "أو اطلب «آية اليوم» أو «سورة الملك».",
            agent=self.name,
        )
