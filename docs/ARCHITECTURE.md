# معمارية «نور الإسلام» (NoorIslamBot)

> هذه الوثيقة تشرح كيف تتحرّك الرسالة داخل المشروع، وما الطبقات المسؤولة عن كل خطوة،
> وكيف يمنع التصميم اختلاق النصوص الشرعية. كل ما يلي مطابق للكود في `app/`.

## 1. المبادئ التصميمية

1. **الفصل التام عن تيليجرام**: لا يستورد أي ملف في `app/agents/` أو `app/services/`
   مكتبة `aiogram`. التعامل مع تيليجرام محصور في طبقة المعالجات (handlers). ولذلك
   يمكن اختبار كل وكيل مباشرةً عبر `pytest` بلا بوت.
2. **النصوص من الأدوات، لا من النموذج**: مسار الذكاء الاصطناعي مُلزَم باستدعاء أدوات
   (tool calling) تُرجِع نصوصاً من مصدر حقيقي، ثم يُفحَص ردّه النهائي بحاجز المخرجات.
3. **التدهور اللطيف**: لا شبكة؟ لا مفتاح LLM؟ لا بيانات؟ كل حالة لها سلوك محدَّد
   ومهذَّب، ولا يرى المستخدم أثراً تقنياً (traceback) أبداً.
4. **الأرخص أولاً في التوجيه**: تعليمة صريحة ← مرجع آية ← مطابقة كلمات ← تصنيف
   بـLLM ← وكيل افتراضي.

## 2. مخطط تدفّق الرسالة

```
                         ┌──────────────────────────────────┐
   تيليجرام (Update) ───▶ │ app/middlewares.py + handlers    │
   رسالة نصّية أو ضغطة زر │  • حقن UserProfile من db (وسطاء) │
                         │  • بناء AgentRequest             │
                         └──────────────┬───────────────────┘
                                        │
                                        ▼
                      ┌──────────────────────────────────────┐
                      │ guardrails.precheck()  (اختياري قبل  │
                      │ التوجيه: إيذاء نفس، سحر، طائفية)      │
                      └──────────────┬───────────────────────┘
                                     │
                                     ▼
                      ┌──────────────────────────────────────┐
                      │ router.route()     app/router.py     │
                      │ 1) explicit_route: /أمر أو ag:…      │
                      │ 2) ayah_reference_route: 2:255…      │
                      │ 3) keyword_route: score ≥ 1.0        │
                      │ 4) llm_route: تصنيف JSON (اختياري)   │
                      │ 5) Route(agent="scholar") افتراضاً   │
                      └──────────────┬───────────────────────┘
                                     │ Route(agent, args)
                                     ▼
                      ┌──────────────────────────────────────┐
                      │ الوكيل: Agent.handle(AgentRequest)   │
                      │ quran|hadith|adhkar|prayer|occasions │
                      └──────────────┬───────────────────────┘
                                     │ AgentReply(text, buttons, sources, audio…)
                                     ▼
      ┌────────────────────────────────────────────────────────────┐
      │ إن كان المسار مسار LLM (وكيل العلم):                        │
      │   guardrails.apply_output_guard(text, had_sources=…)        │
      │   → إلحاق تحذير إن وُجدت نسبة بلا مرجع                      │
      └──────────────┬─────────────────────────────────────────────┘
                     ▼
      ┌────────────────────────────────────────────────────────────┐
      │ app/text.py: esc() للحماية من HTML، chunk() لتقطيع > 4096    │
      │ ثم إرسال الرسائل/الصوت/الأزرار عبر aiogram                  │
      └────────────────────────────────────────────────────────────┘
```

## 3. طبقات المشروع

| الطبقة | الملف | المسؤولية | ملاحظات تصميم |
|---|---|---|---|
| النماذج | `app/models.py` | `AgentRequest`, `AgentReply`, `Button`, `UserProfile`, `Source`, `ParseMode` | مستقلة تماماً عن تيليجرام؛ `Button.callback_data` يقصّ إلى 64 بايت بحدّ تيليجرام |
| النصوص | `app/text.py` | `normalize_arabic` (تطبيع أ/إ/آ/ى/ة + حذف التشكيل والتطويل)، `esc` (HTML)، `chunk` (تقطيع ≤٤٠٩٦)، `to_arabic_digits`، `clean`، `render` (قوالب آمنة) | التطبيع للبحث فقط، والعرض يحفظ النصّ الأصلي |
| الشبكة | `app/services/http.py` | عميل `httpx` مشترك: كاش TTL في الذاكرة، إعادة محاولة ٣ مرّات بتراجع أسّي `0.4·2^n`، تجاهل أخطاء 5xx وإعادة المحاولة | كل الوصول للإنترنت يمرّ من هنا، ما يجعل الحقن في الاختبارات ممكناً |
| الخدمات | `app/services/quran_api.py`, `hadith_api.py`, `prayer_api.py` | تغليف المصادر الخارجية وتحويلها إلى صيغ موحّدة، وإخفاء أخطاء الشبكة | `PrayerAPIError` لأخطاء المواقيت؛ hadith يخزّن على القرص؛ quran يقرأ ملفاً محلياً أولاً |
| قاعدة البيانات | `app/db.py` | SQLite عبر `aiosqlite`، WAL، جداول: `users`, `counters`, `history`, `push_state` | ذاكرة الحوار تُقلَّم إلى ١٢ رسالة/محادثة، وتُقرأ ٦ في مسار LLM و٨ في `get_history` |
| الذكاء الاصطناعي | `app/llm.py` | عميل متوافق مع OpenAI (`/chat/completions`) + دعم `tools` | `MAX_TOOL_ROUNDS=3`، مهلة ٩٠ ثانية، إضافة ترويسات OpenRouter، `LLMError` موحّد |
| الحواجز | `app/guardrails.py` | `precheck`, `detect_fatwa`, `detect_sensitive`, `guard_text`, `unsourced_attributions`, `apply_output_guard` | مستقل عن أي مكتبة؛ سرّ مصداقية البوت |
| الموجّه | `app/router.py` | `explicit_route`, `keyword_route`, `llm_route`, `route`, `fallback_route`, `COMMANDS` | ترتيب الأرخص إلى الأغلى؛ `KEYWORD_THRESHOLD=1.0` |
| الوكلاء | `app/agents/*.py` + `__init__.py` | ستّة وكلاء + سجلّ (`register`, `get_agent`, `all_agents`, `AGENT_ORDER`) | `DEFAULT_AGENT="scholar"`؛ ترتيب `AGENT_ORDER` يُستعمل عند تعادل الدرجات |
| الوسائط (middlewares) | `app/middlewares.py` | `UserMiddleware` يحقن `UserProfile` في `data` قبل كل معالج، و`ErrorMiddleware` يمنع تسرّب أي traceback إلى المستخدم | الوسيطان `outer_middleware` على `message` و`callback_query` |
| البوت والتجهيز | `app/bot.py` | `create_dispatcher` (يضيف الوسائط و`setup_routers`)، `set_commands` (`COMMANDS` بـ`set_my_commands`)، `set_profile` (الوصف والوصف المختصر)، `close_clients` | `COMMANDS` هي المصدر الوحيد لقائمة الأوامر المسجّلة |
| نقطة الدخول | `app/main.py` | `setup_logging`, `validate_settings`, `prepare`, `run_polling`, `run_webhook` | وضعان: `RUN_MODE=polling` (افتراضاً) و`webhook` عبر `aiohttp`+`SimpleRequestHandler`؛ يُنشئ مهمّة المُجدول ويُلغيها في `finally` |
| المُجدول | `app/scheduler.py` | `run_scheduler` يحسب وقت كل مستخدم ويعرض `SCHEDULE`، و`db.push_done` يمنع التكرار | يعمل فقط عند `DAILY_PUSH=true` |
| لوحات المفاتيح | `app/keyboards.py` | `keyboard` (تحويل `Button` محلية إلى `InlineKeyboardButton`)، `main_menu`, `settings_menu`, `back_menu`, `examples_menu`, `AGENT_LABELS` | تفصل بناء الواجهة عن منطق الوكلاء |
| المعالجات | `app/handlers/` | ترجمة تحديثات aiogram إلى `AgentRequest` وإرسال `AgentReply` | **منفَّذة**: الترتيب في `setup_routers` — انظر القسم ٤ |

## 4. طبقة تيليجرام

### 4.1 ترتيب الراوترات (مسار التحديث داخل `app/handlers/`)

`app/bot.py:create_dispatcher()` يبني `Dispatcher`، ويضيف `ErrorMiddleware` ثم
`UserMiddleware` على `message` و`callback_query`، ثم يستدعي `setup_routers(dp)` في
`app/handlers/__init__.py` الذي يُدرج الراوترات بهذا الترتيب المقصود (الأولوية للأعلى):

| # | الراوتر | الملف | يستقبل |
|---|---|---|---|
| ١ | commands | `handlers/commands.py` | الأوامر: `/start`, `/menu`, `/quran`… مع المرادفات العربية |
| ٢ | admin | `handlers/admin.py` | `/stats`, `/broadcast` — للمشرفين فقط (`ADMIN_IDS`) |
| ٣ | menu | `handlers/menu.py` | `menu:*` و`ex:*` و`set:*` |
| ٤ | agent_callbacks | `handlers/agent_callbacks.py` | `ag:*` |
| ٥ | text | `handlers/text.py` | كل نصّ آخر (`F.text`) والوسائط غير المدعومة |

فالمسار المنطقي للمستخدم: **أمر ← قائمة (`menu:*`/`ex:*`) ← زرّ وكيل (`ag:*`) ← نصّ
حرّ**. ولا تنفّذ الراوترات منطق الوكلاء؛ بل تُترجم كلّها إلى `AgentRequest` ثم تستدعي
`handlers/core.process`/`build_request`، فيبقى الوكلاء مستقلّين تماماً عن aiogram.

### 4.2 دور كل ملف من `app/handlers/`

- **`commands.py`**: رسائل الترحيب (`WELCOME`) والمساعدة (`HELP`) و`/about` و`/id`،
  و`/city` التي تبحث عن الإحداثيات في `prayer_api.find_city_coords` ثم تحفظها،
  و`/settings`، والأوامر الوكيلية (`_agent_command`) التي تنفّذ النصّ الافتراضي من
  `DEFAULT_PROMPTS` عند غياب الوسائط، و`/agents`.
- **`menu.py`**: القائمة الرئيسية (`main_menu`)، وقوائم الإعدادات، وأمثلة كل وكيل
  (`examples_menu` ← `ex:<agent>:<index>` تُنفَّذ كنصّ حقيقي عبر `build_request`)،
  وتبديل المدينة/الطريقة/القارئ/الإشعارات عبر `set:method:*` و`set:reciter:*`
  و`set:push`.
- **`agent_callbacks.py`**: يخدم `ag:*` فقط؛ يمرّر المعرّف إلى `callbacks.build_request`،
  ويستشير `callbacks.special_reply` أولاً للحالات التي لا يغطّيها نصّ الوكيل.
- **`callbacks.py`**: **الموضع الوحيد الذي يعرف عقد الوسائط الخاصّ بكل وكيل**.
  القاعدة العامة: `ag:<agent>:<rest>` ← `AgentRequest(text="", args=rest, raw_command=data)`،
  مع استثناءين:
  - `quran`: يُترجَم النداء إلى نصّ عربي يفهمه الوكيل بدل تمرير الوسائط
    (`ag:quran:random` ⇐ «آية اليوم»، `ag:quran:tafsir:2:255` ⇐ «تفسير 2:255»،
    `ag:quran:audio:1:1` ⇐ «تلاوة 1:1»، `ag:quran:surah:67` ⇐ «سورة 67»…).
  - `hadith`: يقرأ الوكيل النداء كاملاً من `args`، فيمرَّر `data` كما هو
    (`ag:hadith:get:bukhari:1`) لأن `router.explicit_route` يقصّ البادئة عند التوجيه
    الافتراضي؛ وهذا الفرق محصور في هذا الملف فلا يتسرّب إلى الوكلاء.

  **مهمّ للمطوّر**: أي وكيل جديد يظهر تلقائياً في القوائم (`menu:*`) وفي أوامر الوكلاء
  وفي `/agents`، لكن إن احتاج عقداً خاصاً لنداءات أزراره فأضِفه في `build_request` هنا
  لا في معالج الراوتر.
- **`text.py`**: آخر ملاذ — كل نصّ غير مطابق؛ يرفض النصّ الأقصر من حرفين، ويجيب على
  الصوت/الصورة/الملف/الفيديو بأن البوت يعمل بالنصوص فقط.
- **`core.py`**: الحواجز (`guardrails.precheck`) ثم `router.route` ثم اختيار الوكيل من
  السجلّ و`agent.handle`، مع `try/except` يُرجِع ردّاً مهذَّباً. و`DEFAULT_PROMPTS`
  و`build_request` و`process`/`process_text` مركزية لكلّ الراوترات.
- **`render.py`**: `chunk` لتقطيع النصّ الطويل، ثم `keyboard(reply.buttons)`، ثم إرسال
  `audio_url` إن وُجد، مع إعادة الإرسال بلا تنسيق عند رفض تيليجرام لـHTML،
  و`replace_reply` للتعديل على رسالة الزر. وهي مسار الإرسال الوحيد لردود الوكلاء.
- **`admin.py`**: `/stats` (المستخدمون والمشتركون والرسائل) و`/broadcast` (بثّ بفاصل
  زمني كل ٢٥ مرسلاً)، وكلاهما يرجع مبكراً إن لم يكن المرسل في `settings.admin_ids`.

### 4.3 دورة الإشعارات المجدولة

عند `DAILY_PUSH=true` يُنشئ `app/main.py` مهمّة `asyncio.create_task(run_scheduler(bot))`
قبل بدء polling/webhook، ويُلغيها في `finally` عند الإيقاف. الحلقة (`app/scheduler.py`)
تستيقظ كل `CHECK_INTERVAL=60` ثانية، وتقرأ `db.subscribers()`، ولكل مستخدم فعّل
`daily_push` تحسب الوقت بتوقيته (`ZoneInfo(user.tz or settings.default_tz)`) وتقارن
بـ`SCHEDULE`:

| `kind` | الوقت | التفضيل المطلوب |
|---|---|---|
| `prayer` | ٥:٠٠ | `push_prayer` |
| `adhkar_morning` | ٦:٣٠ | `push_adhkar` |
| `quran` | ٨:٠٠ | `push_quran` |
| `adhkar_evening` | ١٨:٣٠ | `push_adhkar` |

تُبنى الرسالة بلا تكرار لمنطق الوكلاء: `build_request(prompt, …)` مع `raw_command`
النوع المناسب ثم `process(request)`، ويُرسَل الناتج مع ترويسة «🔔 إشعارك اليومي» وزرّين
للإشعارات والقائمة. و`db.push_done(chat_id, kind, day)` يتحقّق من جدول `push_state`
ويسجّل الإرسال، فيمنع التكرار حتى لو أُعيد تشغيل العملية مرّات في اليوم نفسه. وبين
الرسائل `asyncio.sleep(0.2)` لاحترام حدود تيليجرام.

هذه الحلقة **تحتاج عملية دائمة**: في وضع webhook على منصّة تُوقِف الخدمة لا تعمل
المهمّة، فتبقى `DAILY_PUSH=false` أو تُشغَّل العملية دائماً (انظر `docs/DEPLOY.md`).

## 5. سياسة «لا هلوسة»

المشكلة الأخطر في بوت شرعي هي أن «يُنشئ» النموذج آيةً أو حديثاً. المعالجة على ثلاث
طبقات:

1. **قيود النموذج (system prompt)** في `ScholarAgent.SYSTEM_PROMPT`: لا فتوى، لا اختلاق
   نصّ، الاقتباس فقط من نتائج الأدوات، التصريح بـ«لم أجد نصاً» عند عدم وجود نتيجة،
   تسمية القائل عند نقل خلاف، والتعامل الرحيم مع الأسئلة الحسّاسة.
2. **الأدوات إلزامية عملياً**: `_tool_schemas()` تُعرّف ٨ أدوات
   (`quran_search`, `quran_ayah`, `quran_tafsir`, `hadith_search`, `hadith_get`,
   `adhkar_search`, `prayer_times`, `hijri_date`)، و`_tool_impls()` تنفّذها و**تسجّل
   المصادر المستعملة** في `collected["used"]` و`collected["sources"]`. وكل تنفيذ يضع
   وسم المصدر الحقيقي (مثل «القرآن الكريم — رواية حفص (api.alquran.cloud)»).
3. **حاجز المخرجات**: `apply_output_guard(text, had_sources=…)`:
   - إن استُعملت أداة (`had_sources=True`) فالردّ يمرّ كما هو، ويُعرض في آخره قسم
     «📚 المصادر المستعملة».
   - إن لم تُستعمل أي أداة، يفحص `unsourced_attributions` وجود **نسبة** للنبي ﷺ أو لله
     تعالى (`_HADITH_CLAIM`, `_AYAH_CLAIM`) **بلا** علامة مرجع (`SOURCE_MARKERS` مثل
     «رواه/البخاري/مسلم/رقم» أو `_SURAH_MARK` مثل `[النساء: 29]`)؛ عندها يُلحَق
     `DISCLAIMER` الصريح، ويُسجَّل تحذير `unsourced_attribution`.

وفي المسار بلا LLM لا وجود للنموذج أصلاً: `_offline_answer` يعرض مقاطع جاهزة من
القرآن/الحديث/الأذكار بمراجعها (`📖 من القرآن`, `📜 من السنة`, `📿 من الأذكار`)، أو
يقول صراحةً إنه لم يجد نصاً مباشراً.

**جدار الفتوى**: `detect_fatwa` يطابق أنماطاً مثل `حكم`, `هل يجوز`, `حرام`, `طلاق`,
`ميراث`, `كفارة`, `تكفير`؛ وعند المطابقة يُعاد `FATWA_REPLY` مباشرة بلا استدعاء LLM.
كما تُعالَج فئات `self_harm` و`sihr_general` و`sectarian` بردود مخصّصة.

## 6. سلوك التدهور اللطيف

| الحالة | السلوك المنفَّذ |
|---|---|
| لا مفتاح LLM (`llm.enabled == False`) | `ScholarAgent` يستدعي `_offline_answer`: بحث في القرآن (محلي) ثم الحديث ثم الأذكار، بحدّ ٣ نتائج لكل مصدر، مع وسوم المصادر وأزرار متابعة |
| فشل نداء النموذج (`LLMError`) | الردّ يتحوّل إلى نتائج البحث المباشر مسبوقاً: «⚠️ تعذّر الوصول إلى المحرّك الحواري الآن…» |
| خطأ غير متوقّع في أي وكيل | `try/except` عام في كل وكيل يُعيد رسالة عربية مهذّبة (أو رسالة إعادة المحاولة) بدل الاستثناء |
| لا شبكة في التفسير | `QuranAgent._tafsir` يعرض الآية نفسها (من الملف المحلي) ثم: «تعذّر جلب التفسير الآن…» |
| لا شبكة في المواقيت | `PrayerAPIError` → «⚠️ تعذّر الوصول إلى خدمة المواقيت الآن» مع زر إعادة المحاولة |
| لا شبكة في الحديث | تعليق: «تعذّر الوصول إلى مصدر الحديث الآن…» أو «…جرّب مصدراً آخر» |
| غياب `app/data/adhkar.json` | يتحقّق `AdhkarAgent` من الملف ويُرشد إلى `python scripts/fetch_adhkar.py` |
| غياب ملف القرآن | `quran_api.load_quran` يجلبه من الشبكة بنفس الشكل، و`_run_sync` يعالج حالة وجود Loop قائم بالتشغيل في خيط منفصل |
| تعذّر حفظ تفضيلات المستخدم | `PrayerAgent._resolve` يحفظ المدينة بأفضل جهد (`try/except: pass`) فلا يُفشل الردّ |

## 7. تصميم التخزين المؤقت

ثلاث طبقات متكاملة:

1. **كاش الشبكة في الذاكرة** (`CachedHTTP`): مفتاح = URL + وسائط مرتّبة، مع `hits/misses`.
   TTL الافتراضي `900` ثانية (١٥ دقيقة)، و`get_bytes` (الصوت) `3600`. يُبطَل الخزن عند
   `ttl=None`.
2. **كاش الخدمات الدائمة**:
   - المواقيت: `TTL_TIMINGS = 600` (١٠ دقائق) للتقويم اليومي، و`TTL_STATIC = 86400`
     (يوم) للقبلة/الهجري/إحداثيات المدن.
   - الحديث: نزول الكتاب كاملاً بحدّ TTL يومي، مع **كتابة ذرّية على القرص**
     (`tmp` ثم `os.replace`) في `data/cache/hadith/ara-<key>.json`، وقفل `asyncio.Lock`
     لكل كتاب حتى لا يتنزّل مرّتين بالتوازي، مع LRU في الذاكرة: `_BOOKS_MAX=3` كتب
     و`_INDEX_MAX=2` فهارس بحث. وجود الملف على القرص يُلغي الحاجة للشبكة تماماً.
   - القرآن: `lru_cache(maxsize=1)` لـ`load_quran` و`_index` و`_english_index` —
     تحميل مرة واحدة للعملية (١.٤ ميغابايت تقريباً).
3. **البيانات المُجمَّعة الثابتة**: `app/data/*.json` تُتتبَّع في Git وتُنسخ في صورة
   Docker، لأنها صغيرة ومنتِجها سكربتان موثَّقان (`scripts/fetch_quran.py`,
   `fetch_adhkar.py`). أما `data/cache/` و`data/noor.db` فليست كذلك (انظر `.gitignore`).

## 8. إضافة وكيل جديد

الوكلاء يسجّلون أنفسهم في سجلّ واحد، والموجّه يكتشفهم تلقائياً — لا تعديل في
`router.py` مطلوب.

1. أنشئ الملف `app/agents/<name>.py` وارث `BaseAgent`:

```python
from ..models import AgentReply, AgentRequest, Button
from .base import BaseAgent


class DuhaAgent(BaseAgent):
    name = "duha"                     # يُستعمل في ag:<name>:… وفي الموجّه
    title = "وكيل الضحى"
    description = "أذكار الضحى وصلاة الضحى وتنبيهاتها."
    keywords = ["الضحى", "صلاة الضحى", "أذكار الضحى"]   # تُستعمل في المطابقة
    examples = ["أذكار الضحى", "صلاة الضحى"]

    async def handle(self, request: AgentRequest) -> AgentReply:
        if (request.args or "").startswith("ag:duha:"):
            return self._on_callback(request.args)
        try:
            ...
        except Exception:
            # لا traceback للمستخدم أبداً
            return AgentReply(text="تعذّر تنفيذ الطلب الآن.", agent=self.name)

    def _on_callback(self, payload: str) -> AgentReply:
        parts = payload.split(":")          # ag : duha : action : args…
        action = parts[2] if len(parts) > 2 else ""
        return AgentReply(
            text="…",
            agent=self.name,
            buttons=[[Button("تحديث 🔄", "ag:duha:refresh")]],
        )
```

2. سجّله في `app/agents/__init__.py`:

```python
from .duha import DuhaAgent

_AGENTS = {
    agent.name: agent
    for agent in (
        PrayerAgent(), QuranAgent(), HadithAgent(), AdhkarAgent(), OccasionsAgent(),
        ScholarAgent(), DuhaAgent(),        # ← الوكيل الجديد
    )
}
```

أو استدعِ `register(DuhaAgent())` عند التشغيل؛ فـ`register()` يضيف الاسم إلى
`AGENT_ORDER` تلقائياً إن كان جديداً.

3. **ما يجب مراعاته**:
   - `keywords`: كلمة أطول = وزن أعلى (`1.0 + min(len,12)/20`، و`+0.4` إن بدأ النصّ
     بالكلمة)، والحدّ الأدنى للقبول `KEYWORD_THRESHOLD = 1.0`. تجنّب كلمات عامة تُنافس
     الوكلاء الحاليين، وإن تعادلتَ مع وكيل آخر فالفاصل هو موضعه في `AGENT_ORDER`.
   - `callback_data` بحدّ ٦٤ بايت؛ استخدم أفعالاً قصيرة واختصر الاستعلامات الطويلة
     (كما يفعل وكيل الحديث عبر `_Shortcuts`).
   - `Button.data` يجب أن يبدأ بـ`ag:<name>:` حتى يفكّه `router.explicit_route`.
   - أضف الرمز إلى `__all__` إن كان الملف يصدّر دوال نقية قابلة للاختبار.
   - أضف اختباراً في `tests/` يعمل بلا شبكة (احقن `http.get_json` بديلاً).

## 9. التخزين وسلامة البيانات

- SQLite بوضع `WAL` و`foreign_keys=ON`. الجداول: `users` (التفضيلات + آخر ظهور)،
  `counters` (عدّاد السبحة)، `history` (ذاكرة الحوار، تُقلَّم إلى ١٢ عنصراً لكل محادثة)،
  `push_state` (منع إرسال الإشعار نفسه مرّتين في اليوم نفسه عبر المفتاح
  `(chat_id, kind, day)`).
- الحقول القابلة للتحديث محصورة في `Database._ALLOWED_FIELDS` (city, country, lat, lon,
  method, reciter, tz, daily_push, first_name) — لا بناء SQL من مدخلات المستخدم.
- `Database.subscribers(kind)` تُرجِع المشتركين في الإشعارات، مع تصفية إضافية حسب نوع
  الإشعار (`push_quran`, `push_adhkar`, `push_prayer`).

## 10. حدود معروفة في هذا الإصدار

- المناسبات تُحسب بنموذج هجري تقريبي (متوسط أطوال الأشهر) وموسومة بذلك في الردّ نفسه،
  والمواقيت تقدير فلكي وفق طريقة الحساب المختارة.
- درجات الحكم في الحديث منقولة كما يعرضها المصدر (`grades`) ولا تُحقَّق داخلياً.
- `SCHOLAR_FOOTER` معرَّف في `app/guardrails.py` ولا يُستدعى حالياً (الردود تمرّ عبر
  `apply_output_guard` و`detect_fatwa` و`guard_text`).
- تغطية الاختبارات تشمل `router.py` و`guardrails.py` و`handlers/core.py`
  و`handlers/callbacks.py` و`db.py`، ولا تشمل `llm.py` تغطيةً مباشرة.
- الإشعارات المجدولة تعمل بالفحص الدوري (كل ٦٠ ثانية) لا بمنبّه نظام دقيق، فقد يصل
  الإشعار بتأخير يصل إلى دقيقة، والإرسال بين المستخدمين تسلسلي (بفاصل ٠.٢ ثانية).
- فرق عقد الوسائط بين الوكلاء (القسم ٤.٢) مقصود ومحصور في `handlers/callbacks.py`؛ أي
  وكيل جديد بنداءات أزرار خاصة يحتاج إضافةً هناك.
