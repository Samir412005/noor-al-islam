"""وكيل العلم — الواجهة الحوارية للبوت.

فلسفته: **يستدعي ولا يخترع**. كل نصّ شرعي يُعرض على المستخدم يأتي من أداة
(القرآن، الحديث، الأذكار، المواقيت) ويُذكر مرجعه. هذا يحوّل مخاطر هلوسة النماذج
اللغوية في أخطر أبوابها (الوحي) إلى مجرّد احتمال خطأ في صياغة الشرح.

بدون مفتاح LLM يبقى الوكيل مفيداً: يتحوّل إلى بحث متعدّد المصادر ويعرض النصوص.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..db import db
from ..guardrails import (
    FATWA_REPLY,
    apply_output_guard,
    detect_fatwa,
    detect_sensitive,
    guard_text,
    verify_quotes,
)
from ..llm import LLMError, llm
from ..models import AgentReply, AgentRequest, Button
from ..services import hadith_api, prayer_api, quran_api
from ..text import bullet_list, clean, esc, normalize_arabic
from . import adhkar as adhkar_agent
from .base import BaseAgent

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """أنت «وكيل العلم» في بوت «نور الإسلام» — رفيقٌ يوميّ للمسلم يقدّم النصوص الشرعية بأمانة.

اتّبع هذه القواعد بلا استثناء:
1) **لا تُفتِ**. إن سُئلت عن حكم شرعي (حلال/حرام/يجوز/كفارة/طلاق/ميراث…) فلا تُصدر حكماً؛ بيّن بلطف أنك لا تُفتي، واذكر النصوص المتّصلة إن وُجدت، وأحِل السائل إلى دار الإفتاء أو عالِم موثوق.
2) **لا تختلق نصاً أبداً**. كل آية أو حديث تعرضه يجب أن يأتي حرفياً من نتائج الأدوات. لا تُغيّر لفظاً ولا تنسب حديثاً بلا مرجع (كتاب + رقم).
3) إن لم تُرجِع الأدوات نصّاً ذا صلة فقل صراحة: «لم أجد نصاً بخصوص ذلك»، ثم قدّم ما تعرفه من معلومات عامة *بوضوح* بعيداً عن النصوص.
4) عند ذكر خلاف أو قول لعالم، سمِّ قائله. ولا تجزم في أمر مختلف فيه.
5) إن كان السؤال عن أذى النفس أو أمر حسّاس، تعامل برحمة واذكر ﴿وَلَا تَقْتُلُوا أَنفُسَكُمْ﴾ [النساء: 29] وانصح بطلب المساعدة.
6) إن سأل عن الغيب أو السحر فذكّر بالتوكّل والرقية المشروعة، وانصح بالبعد عن المشعوذين.
7) **لا تكتب نصّاً قرآنياً أو حديثياً دون استدعاء أداة، واقتبسه حرفياً كما ورد في نتيجة الأداة.** أيّ نصّ منسوب لا يطابق مخرجات الأدوات سيُحذف آليّاً من جوابك.

الأسلوب: عربية فصيحة ميسّرة، إيجاز بلا اختصار مخلّ، نبرة أخٍ حريص. اكتب نصاً عادياً بلا وسوم HTML. استخدم «•» للتعداد عند الحاجة. لا تكتب عناوين زخرفية."""


GREETINGS = {
    "مرحبا", "مرحبه", "السلام عليكم", "سلام", "سلام عليكم", "اهلا", "اهلا وسهلا",
    "صباح الخير", "مساء الخير", "hi", "hello", "شكرا", "شكرا لك", "بارك الله فيك",
    "جزاك الله خيرا", "من انت", "من أنت", "عرف بنفسك",
}


# مقدمات السؤال التي تُنزع قبل البحث
_LEAD = re.compile(
    r"^(?:ما|ماذا|من|كيف|لماذا|لم|هل|معنى|معني|أخبرني|اخبرني|اذكر|أريد|اريد|"
    r"أريد أن أعرف|ابحث|عن|لي\u064e|وضح|أشرح|اشرح|فسر|فسّر|قل)\s+"
)
# ذيول السؤال التي تُنزع قبل البحث
_TAIL = re.compile(
    r"\s*(?:في\s+القرآن(?:\s+الكريم)?|من\s+القرآن|بالقرآن|في\s+الكتاب\s+والسنة)\s*$"
)


def _query_terms(text: str) -> str:
    """ينظّف السؤال ليصلح للبحث: يحذف أدوات الاستفهام وكلمات الحشو."""
    cleaned = clean(text or "").strip(" ؟?!.،:؛")
    for _ in range(5):
        nxt = _LEAD.sub("", cleaned).strip()
        if nxt == cleaned:
            break
        cleaned = nxt
    cleaned = _TAIL.sub("", cleaned).strip().strip(" ؟?!.،:؛")
    return cleaned[:120]


class ScholarAgent(BaseAgent):
    name = "scholar"
    title = "وكيل العلم"
    description = "يجيب عن الأسئلة بالبحث في القرآن والحديث مع ذكر المصادر، ويجمع النتائج من عدة وكلاء."
    examples = [
        "ما معنى الصبر في القرآن؟",
        "اذكر أحاديث عن بر الوالدين",
        "ما فضل قراءة سورة الملك؟",
        "كيف أتوب توبة صادقة؟",
    ]
    keywords = [
        "معنى", "اشرح", "شرح", "وضح", "فسّر", "فسر", "لماذا", "كيف", "ما هو", "ما هي",
        "اخبرني", "أخبرني", "أريد أن أعرف", "اريد ان اعرف", "سؤال", "اسأل", "أفضلية", "فضل",
    ]
    priority = 40

    # ── الواجهة ────────────────────────────────────────────────────
    async def handle(self, request: AgentRequest) -> AgentReply:
        args = (request.args or "").strip().lower()
        if args in {"new", "menu", "help"}:
            return self._help_reply()

        question = clean(f"{request.text or ''} {request.args or ''}")

        if not question:
            return self._help_reply()

        if normalize_arabic(question) in {normalize_arabic(g) for g in GREETINGS}:
            return self._greeting_reply(request)

        if detect_fatwa(question):
            return AgentReply(text=FATWA_REPLY, agent=self.name, sources=["تنبيه: لا فتوى من بوت"])

        sensitive = detect_sensitive(question)
        if sensitive and (guarded := guard_text(sensitive)):
            return AgentReply(text=guarded, agent=self.name)

        if not llm.enabled:
            return await self._offline_answer(question)

        try:
            return await self._llm_answer(request, question)
        except LLMError as exc:
            log.warning("scholar llm failed: %s", exc)
            reply = await self._offline_answer(question)
            reply.text = (
                "⚠️ <i>تعذّر الوصول إلى المحرّك الحواري الآن — وهذه نتائج البحث المباشر:</i>\n\n"
                + reply.text
            )
            return reply
        except Exception:  # pragma: no cover - حماية قصوى
            log.exception("scholar unexpected error")
            return await self._offline_answer(question)

    # ── المسار الذكي (LLM + أدوات) ────────────────────────────────
    async def _llm_answer(self, request: AgentRequest, question: str) -> AgentReply:
        collected: dict[str, Any] = {"sources": [], "used": False, "corpus": []}
        tools = self._tool_schemas()
        impls = self._tool_impls(request, collected)

        history = await db.get_history(request.chat_id, limit=6)
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})

        answer = await llm.chat(
            messages,
            tools=tools,
            tool_impls=impls,
            on_tool_result=lambda _name, result: self._record_result(collected, result),
        )
        if not answer:
            answer = "لم أتمكّن من صياغة جواب. أعد صياغة سؤالك من فضلك."

        # الترتيب مقصود: التهيئة أولاً، ثم مطابقة الشواهد بالمصادر، ثم حارس المصدر
        safe_answer = esc(answer)
        safe_answer, quote_warnings = verify_quotes(safe_answer, "\n".join(collected["corpus"]))
        safe_answer, source_warnings = apply_output_guard(
            safe_answer, had_sources=bool(collected["used"])
        )
        warnings = quote_warnings + source_warnings
        if warnings:
            log.info("guard warnings: %s", warnings)

        await db.add_history(request.chat_id, "user", question)
        await db.add_history(request.chat_id, "assistant", answer)

        sources = collected["sources"][:6]
        body = safe_answer
        if sources:
            body += "\n\n————\n<b>📚 المصادر المستعملة</b>\n" + bullet_list(
                [f"<i>{esc(s)}</i>" for s in sources]
            )

        return AgentReply(
            text=f"<b>🧠 وكيل العلم</b>\n\n{body}",
            agent=self.name,
            sources=sources,
            buttons=self._follow_up_buttons(question),
        )

    @staticmethod
    def _record_result(collected: dict[str, Any], result: str) -> None:
        """يجمع مخرجات الأدوات: هي المرجع الوحيد المسموح للاقتباس."""
        collected["corpus"].append(result)
        if result and not result.startswith(("لا توجد", "لم أجد", "تعذّر")):
            collected["used"] = True

    # ── المسار بلا LLM: بحث متعدّد المصادر ────────────────────────
    async def _offline_answer(self, question: str) -> AgentReply:
        query = _query_terms(question) or question
        if len(normalize_arabic(query)) < 3:
            return self._help_reply()
        blocks: list[str] = []
        sources: list[str] = []

        try:
            ayahs = quran_api.search_ayahs(query, limit=3)
        except Exception:  # pragma: no cover
            ayahs = []
        if ayahs:
            lines = [
                f"﴿ {esc(a['text'][:180])}{'…' if len(a['text']) > 180 else ''} ﴾\n"
                f"<i>[{esc(a['surah_name'])}: {a['ayah']}]</i>"
                for a in ayahs
            ]
            blocks.append("<b>📖 من القرآن</b>\n" + "\n\n".join(lines))
            sources.append(quran_api.SOURCE_QURAN)

        try:
            hadiths = await hadith_api.search_hadith(query, limit=3)
        except Exception:  # pragma: no cover
            hadiths = []
        if hadiths:
            lines = [
                f"{esc(h['text'][:220])}{'…' if len(h['text']) > 220 else ''}\n"
                f"<i>— {esc(hadith_api.reference_text(h))}</i>"
                for h in hadiths
            ]
            blocks.append("<b>📜 من السنة</b>\n" + "\n\n".join(lines))
            sources.append("fawazahmed0/hadith-api (النصوص العربية)")

        try:
            adhkar = adhkar_agent.search_adhkar(query, limit=3)
        except Exception:  # pragma: no cover
            adhkar = []
        if adhkar:
            lines = [
                f"• {esc(hit['item'].get('text', '')[:160])}\n  <i>({esc(hit['category'].get('title', ''))})</i>"
                for hit in adhkar
            ]
            blocks.append("<b>📿 من الأذكار</b>\n" + "\n".join(lines))
            sources.append("حصن المسلم — القحطاني")

        if not blocks:
            return AgentReply(
                text=(
                    "<b>🧠 وكيل العلم</b>\n\n"
                    "لم أجد نصاً مباشراً يخصّ سؤالك في المصادر المتاحة.\n\n"
                    "جرّب أن تصوغه بكلمات أوضح، أو اسأل مثلاً:\n"
                    "• «ابحث عن الصبر»\n• «أحاديث عن بر الوالدين»\n• «أذكار الهم»\n\n"
                    "<i>وملاحظة: لو فُعِّل مفتاح الذكاء الاصطناعي لصار الحوار أوسع "
                    "(انظر <code>LLM_API_KEY</code> في ملف الإعدادات).</i>"
                ),
                agent=self.name,
                buttons=self._follow_up_buttons(question),
            )

        return AgentReply(
            text="<b>🧠 وكيل العلم — نتائج من المصادر</b>\n\n" + "\n\n".join(blocks),
            agent=self.name,
            sources=sources,
            buttons=self._follow_up_buttons(question),
            meta={"offline": True},
        )

    def _greeting_reply(self, request: AgentRequest) -> AgentReply:
        name = request.user.display_name
        return AgentReply(
            text=(
                f"وعليكم السلام ورحمة الله وبركاته يا {esc(name)} 🌿\n\n"
                "أنا «نور الإسلام» — في خدمتك: قرآن بتفسيره، حديث بتخريجه، "
                "أذكار، ومواقيت صلاتك.\n\n"
                "اكتب طلبك أو اختر من القائمة."
            ),
            agent=self.name,
            buttons=[
                [Button("📖 آية اليوم", "ex:quran:0"), Button("📜 حديث اليوم", "ex:hadith:0")],
                [Button("📿 أذكار الصباح", "ex:adhkar:0"), Button("🕌 المواقيت", "ag:prayer:timings")],
                [Button("🧠 اسأل سؤالاً", "menu:scholar")],
            ],
        )

    def _help_reply(self) -> AgentReply:
        return AgentReply(
            text=(
                "<b>🧠 وكيل العلم</b>\n\n"
                "اسألني أي سؤال، وسأبحث لك في القرآن والسنّة وأعرض النصوص بمصادرها.\n\n"
                "أمثلة:\n"
                "• ما معنى الصبر في القرآن؟\n"
                "• اذكر أحاديث عن بر الوالدين\n"
                "• ما فضل سورة الملك؟\n\n"
                "<i>تنبيه: أنا ناقلٌ للنصوص لا مُفتٍ.</i>"
            ),
            agent=self.name,
            buttons=[
                [Button("📖 وكيل القرآن", "ag:quran:random"), Button("📜 وكيل الحديث", "ag:hadith:random")],
                [Button("📿 وكيل الأذكار", "ag:adhkar:random"), Button("🕌 المواقيت", "ag:prayer:timings")],
            ],
        )

    @staticmethod
    def _follow_up_buttons(question: str) -> list[list[Button]]:
        short = (_query_terms(question) or re.sub(r"\s+", " ", question).strip())[:16]
        if not short:
            return []
        return [
            [
                Button("📜 أحاديث ذات صلة", f"ag:hadith:search:{short}"),
                Button("📖 آيات ذات صلة", f"ag:quran:search:{short}"),
            ],
            [Button("🆕 سؤال جديد", "ag:scholar:new")],
        ]

    # ── الأدوات التي يملكها الوكيل ────────────────────────────────
    @staticmethod
    def _tool_schemas() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "quran_search",
                    "description": "يبحث في نصّ القرآن كاملاً ويعيد الآيات المطابقة مع أرقامها.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "كلمات عربية للبحث"},
                            "limit": {"type": "integer", "description": "عدد النتائج (1-6)"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "quran_ayah",
                    "description": "يعيد آية محدّدة برقم السورة ورقم الآية.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "surah": {"type": "integer"},
                            "ayah": {"type": "integer"},
                        },
                        "required": ["surah", "ayah"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "quran_tafsir",
                    "description": "يعيد التفسير المعتمد لآية (الميسّر افتراضاً، أو القرطبي/الجلالين/البغوي/الوسيط).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "surah": {"type": "integer"},
                            "ayah": {"type": "integer"},
                            "kind": {
                                "type": "string",
                                "enum": list(quran_api.TAFSIR_KINDS.keys()),
                                "description": "نوع التفسير",
                            },
                        },
                        "required": ["surah", "ayah"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hadith_search",
                    "description": "يبحث في كتب الحديث (البخاري، مسلم، النووية افتراضاً) ويعيد النصوص مع تخريجها.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "collection": {
                                "type": "string",
                                "enum": list(hadith_api.COLLECTIONS.keys()),
                                "description": "كتاب محدّد (اختياري)",
                            },
                            "limit": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hadith_get",
                    "description": "يعيد حديثاً برقمه من كتاب محدّد.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "collection": {"type": "string", "enum": list(hadith_api.COLLECTIONS.keys())},
                            "number": {"type": "integer"},
                        },
                        "required": ["collection", "number"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "adhkar_search",
                    "description": "يبحث في الأذكار والأدعية المأثورة (حصن المسلم).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "prayer_times",
                    "description": "يعيد مواقيت الصلاة اليوم لمدينة (أو مدينة المستخدم إن أُهملت).",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hijri_date",
                    "description": "يعيد تاريخ اليوم الهجري.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def _tool_impls(self, request: AgentRequest, collected: dict[str, Any]):
        """يبني الدوال التنفيذية للأدوات، ويجمع المصادر المستعملة."""

        def mark(*sources: str) -> None:
            collected["used"] = True
            for source in sources:
                if source and source not in collected["sources"]:
                    collected["sources"].append(source)

        async def quran_search(query: str, limit: int = 4) -> str:
            results = quran_api.search_ayahs(query, limit=max(1, min(int(limit or 4), 6)))
            if not results:
                return "لا توجد نتائج."
            mark(quran_api.SOURCE_QURAN)
            return "\n\n".join(
                f"[{r['surah_name']}: {r['ayah']}] {r['text']}" for r in results
            )

        async def quran_ayah(surah: int, ayah: int) -> str:
            try:
                a = quran_api.get_ayah(int(surah), int(ayah))
            except (ValueError, TypeError):
                return "مرجع الآية غير صحيح."
            mark(quran_api.SOURCE_QURAN)
            return f"[{a['surah_name']}: {a['ayah']}] {a['text']}"

        async def quran_tafsir(surah: int, ayah: int, kind: str = "ar.muyassar") -> str:
            if kind not in quran_api.TAFSIR_KINDS:
                kind = "ar.muyassar"
            try:
                a = quran_api.get_ayah(int(surah), int(ayah))
                text = await quran_api.tafsir(int(surah), int(ayah), kind)
            except (ValueError, TypeError):
                return "تعذّر جلب التفسير."
            except Exception as exc:  # pragma: no cover
                return f"تعذّر جلب التفسير: {type(exc).__name__}"
            mark(quran_api.SOURCE_QURAN, f"{quran_api.TAFSIR_KINDS[kind]} — api.alquran.cloud")
            return f"[{a['surah_name']}: {a['ayah']}] {a['text']}\n\n{text}"

        async def hadith_search(query: str, collection: str | None = None, limit: int = 4) -> str:
            key = None
            if collection:
                key = collection if collection in hadith_api.COLLECTIONS else hadith_api.find_collection(collection)
            try:
                results = await hadith_api.search_hadith(query, key=key, limit=max(1, min(int(limit or 4), 6)))
            except Exception as exc:  # pragma: no cover
                return f"تعذّر البحث في الحديث: {type(exc).__name__}"
            if not results:
                return "لا توجد نتائج."
            mark("fawazahmed0/hadith-api (النصوص العربية)")
            return "\n\n".join(
                f"{h['text']}\n— {hadith_api.reference_text(h)}" for h in results
            )

        async def hadith_get(collection: str, number: int) -> str:
            key = collection if collection in hadith_api.COLLECTIONS else hadith_api.find_collection(collection)
            if not key:
                return "كتاب الحديث غير معروف."
            try:
                hadith = await hadith_api.get_hadith(key, int(number))
            except Exception as exc:  # pragma: no cover
                return f"تعذّر جلب الحديث: {type(exc).__name__}"
            if not hadith:
                return "لم أجد هذا الحديث."
            mark("fawazahmed0/hadith-api (النصوص العربية)")
            return f"{hadith['text']}\n— {hadith_api.reference_text(hadith)}"

        async def adhkar_search(query: str, limit: int = 3) -> str:
            try:
                hits = adhkar_agent.search_adhkar(query, limit=max(1, min(int(limit or 3), 5)))
            except Exception:  # pragma: no cover
                return "بيانات الأذكار غير متاحة."
            if not hits:
                return "لا توجد نتائج."
            mark("حصن المسلم — القحطاني")
            return "\n\n".join(
                f"{hit['item'].get('text', '')}\n({hit['category'].get('title', '')})" for hit in hits
            )

        async def prayer_times(city: str | None = None) -> str:
            lat, lon, method = request.user.lat, request.user.lon, request.user.method
            label = request.user.city
            if city:
                found = await prayer_api.find_city_coords(city)
                if found:
                    lat, lon, label = found["latitude"], found["longitude"], found.get("name") or city
            data = await prayer_api.timings(lat, lon, method=method)
            mark("AlAdhan API — حساب المواقيت")
            rows = " • ".join(f"{name}: {value}" for name, value in data["ordered"])
            return f"مواقيت الصلاة في {label} ({data.get('date_text', '')}): {rows}"

        async def hijri_date() -> str:
            data = await prayer_api.hijri()
            mark("تقويم أم القرى (تحويل هجري)")
            return data.get("text", "")

        return {
            "quran_search": quran_search,
            "quran_ayah": quran_ayah,
            "quran_tafsir": quran_tafsir,
            "hadith_search": hadith_search,
            "hadith_get": hadith_get,
            "adhkar_search": adhkar_search,
            "prayer_times": prayer_times,
            "hijri_date": hijri_date,
        }
