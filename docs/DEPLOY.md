# دليل النشر — بوت «نور الإسلام» (NoorIslamBot)

> ### ⚠️ قبل البدء: أمن الأسرار
> - **لا تشارك `BOT_TOKEN` مع أي جهة، ولا تضعه في Git، ولا في مستودع عام، ولا في لقطة
>   شاشة، ولا في دردشة.** من يملك التوكن يملك البوت بالكامل.
> - ضع التوكن في ملف `.env` على الخادم فقط، بصلاحيات `chmod 600 .env`، وأضِف `.env`
>   إلى `.gitignore` (موجود مسبقاً).
> - في وضع webhook استعمل `WEBHOOK_SECRET` دائماً، وتحقّق من ترويسة تيليجرام
>   `X-Telegram-Bot-Api-Secret-Token` في كل طلب.
> - عند أي شكّ في تسريب التوكن: أبطِله فوراً من @BotFather (`/revoke`) وأصدر غيره
>   (انظر القسم «الوضع الحاليّ للبوت الأصلي»).
> - لا تمرّر التوكن في سطر أوامر يُسجَّل في `bash_history` أو `ps`؛ استعمله من متغيّر
>   بيئة (`$BOT_TOKEN`) أو من ملف الإعدادات.

## 0. تحقّق قبل النشر

لا تنشر قبل أن تنجح هذه الخطوات بالترتيب، على نسخة مطابقة للمستودع:

```bash
python -m pytest -q                 # ١) كل الاختبارات (بلا شبكة) — يجب أن تنجح كلها
python scripts/smoke.py --offline   # ٢) اختبار دخاني بلا شبكة لكل الوكلاء
python scripts/smoke.py             # ٣) نفس الحالات عبر الشبكة الحقيقية
```

- `pytest` يقرأ `asyncio_mode=auto` و`testpaths=tests` من `pytest.ini`؛ والعدد الحالي
  **230 اختباراً**، كلها بلا شبكة (تحجب عميل HTTP بالحقن).
- `smoke.py --offline` يُبطل الوصول إلى خدمات القرآن/الحديث/الأذكار بالحقن ويطبع
  «النتيجة: N/N»؛ و`smoke.py` بلا `--offline` يمرّر الحالات نفسها عبر الشبكة ويحتاج
  وصولاً إلى `api.alquran.cloud` و`api.aladhan.com` و`cdn.jsdelivr.net`.
- كلاهما يرجع رمز خروج غير صفري عند أي فشل، فيصلح للاستعمال في CI قبل النشر.
- ثم شغّل `python scripts/healthcheck.py --offline` في بيئة النشر نفسها للتأكد من
  ملفات البيانات والذاكرة وقاعدة الكتابة في `data/`.

> **تذكير الإشعارات المجدولة**: `DAILY_PUSH=true` يتطلّب **عملية دائمة** حتى في وضع
> webhook؛ إن كانت منصّتك تُوقِف الخدمة عند عدم الطلب فاضبط `DAILY_PUSH=false` أو شغّل
> عملية دائمة منفصلة. التفصيل في القسم ٤.٤.

## 1. متطلَّبات النشر

- Python 3.10+ (أو Docker).
- ملف `.env` مبنيّ من `.env.example` ويحتوي `BOT_TOKEN` على الأقل.
- البيانات المُجمَّعة: إمّا نسخ `app/data/*.json` مع الكود، أو تشغيل
  `python scripts/fetch_quran.py` و`python scripts/fetch_adhkar.py` بعد النشر.
- مجلّد دائم للكتابة يحتوي: قاعدة SQLite (`DB_PATH`) و`data/cache/hadith/`.
- نقطة الدخول `app/main.py` **موجودة وجاهزة** (`python -m app.main`)، وكذلك طبقة
  `app/handlers/` بالكامل، ولا يتوقّف النشر على أي عمل تنفيذ إضافي (انظر `docs/ARCHITECTURE.md`).

**اختيار الوضع**:
- `RUN_MODE=polling`: لا يحتاج عنواناً عاماً ولا منفذاً؛ يحتاج عملية دائمة.
- `RUN_MODE=webhook`: يحتاج `WEBHOOK_BASE_URL` (HTTPS)، و`WEBHOOK_SECRET`، ومنفذاً
  مفتوحاً (`PORT`). مناسب للمنصّات التي تُوقظ الخدمة عند الطلب.

استعمل دائماً سكربت المشروع بدل `curl` اليدوي:

```bash
python scripts/setup_webhook.py --info      # عرض حالة الـwebhook بلا طباعة التوكن
python scripts/setup_webhook.py             # setWebhook من .env
python scripts/setup_webhook.py --delete    # deleteWebhook (رجوعاً إلى polling)
```

---

## 2. المسار الأول: VPS + systemd

### 2.1 التجهيز

```bash
sudo useradd --system --create-home --home-dir /opt/noor --shell /usr/sbin/nologin noor
sudo git clone <repo-url> /opt/noor/noor-islam-bot
cd /opt/noor/noor-islam-bot
sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt

sudo -u noor cp .env.example .env
sudo -u noor nano .env          # BOT_TOKEN إلزامي، واضبط الباقي
sudo chmod 600 .env
sudo chown -R noor:noor /opt/noor/noor-islam-bot

sudo -u noor .venv/bin/python scripts/fetch_quran.py
sudo -u noor .venv/bin/python scripts/fetch_adhkar.py
sudo -u noor .venv/bin/python scripts/healthcheck.py --offline
```

### 2.2 وحدة systemd

أنشئ الملف `/etc/systemd/system/noor-islam-bot.service`:

```ini
[Unit]
Description=Noor Islam Telegram Bot (NoorIslamBot)
Documentation=https://example.invalid/noor-islam-bot/docs/DEPLOY.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=noor
Group=noor
WorkingDirectory=/opt/noor/noor-islam-bot

# الإعدادات من ملف .env فقط (EnvironmentFile يقبل KEY=value)
EnvironmentFile=/opt/noor/noor-islam-bot/.env
# املأه إمّا هنا أو في .env — polling افتراضاً:
# Environment=RUN_MODE=webhook
# Environment=WEBHOOK_BASE_URL=https://bot.example.com
# Environment=WEBHOOK_PATH=/telegram/webhook
# Environment=WEBHOOK_SECRET=<سرّ طويل عشوائي>
# Environment=PORT=8080

ExecStart=/opt/noor/noor-islam-bot/.venv/bin/python -m app.main
Restart=always
RestartSec=5
TimeoutStopSec=20
KillSignal=SIGINT

# صلابة أمنية
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/noor/noor-islam-bot/data

# سجلّات → journald
StandardOutput=journal
StandardError=journal
SyslogIdentifier=noor-islam-bot

[Install]
WantedBy=multi-user.target
```

وإن أردت فحصاً دورياً بجدولة زمنية (فحص البيانات والذاكرة، ومع `getMe` إن وُجد
`BOT_TOKEN`) فهذا ملف `noor-islam-bot-healthcheck.service`:

```ini
[Unit]
Description=Healthcheck for NoorIslamBot
After=noor-islam-bot.service

[Service]
Type=oneshot
User=noor
WorkingDirectory=/opt/noor/noor-islam-bot
EnvironmentFile=/opt/noor/noor-islam-bot/.env
ExecStart=/opt/noor/noor-islam-bot/.venv/bin/python scripts/healthcheck.py
SyslogIdentifier=noor-islam-bot-health
```

وملف `noor-islam-bot-healthcheck.timer`:

```ini
[Unit]
Description=Run NoorIslamBot healthcheck every 15 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=15min
Unit=noor-islam-bot-healthcheck.service

[Install]
WantedBy=timers.target
```

### 2.3 التشغيل

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now noor-islam-bot.service
sudo systemctl status noor-islam-bot.service --no-pager
journalctl -u noor-islam-bot -f --output=cat

# الفحص الدوري (اختياري)
sudo systemctl enable --now noor-islam-bot-healthcheck.timer
systemctl list-timers noor-islam-bot-healthcheck.timer
```

### 2.4 وضع webhook على VPS

1. اجعل الخدمة تُصغي على `PORT=8080` خلف وكيل عكسي (nginx/caddy) مع HTTPS.
2. اضبط في `.env`: `RUN_MODE=webhook`, `WEBHOOK_BASE_URL=https://bot.example.com`,
   `WEBHOOK_PATH=/telegram/webhook`, `WEBHOOK_SECRET=<عشوائي طويل>`.
3. مثال nginx (السرّ يُتحقَّق منه داخل التطبيق، وهذه الترويسات تُمرَّر كاملة):

```nginx
location /telegram/webhook {
    proxy_pass         http://127.0.0.1:8080/telegram/webhook;
    proxy_http_version 1.1;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_set_header   X-Telegram-Bot-Api-Secret-Token $http_x_telegram_bot_api_secret_token;
    proxy_read_timeout 90s;
}
```

4. أنشئ الـwebhook:

```bash
cd /opt/noor/noor-islam-bot && set -a && . ./.env && set +a
python scripts/setup_webhook.py
python scripts/setup_webhook.py --info
```

5. للرجوع إلى polling في أي وقت: `python scripts/setup_webhook.py --delete` ثم
   `sudo systemctl restart noor-islam-bot`.

> ملاحظة جدار الحماية: افتح `443` فقط للعالم، وأبقِ `8080` محلياً
> (`ufw allow 443/tcp`، ولا تفتح `8080`). وفي وضع polling لا حاجة لفتح أي منفذ.

---

## 3. المسار الثاني: Docker

### 3.1 بالصورة المفردة

```bash
cd noor-islam-bot
cp .env.example .env && nano .env      # BOT_TOKEN إلزامي
chmod 600 .env

docker build -t noor-islam-bot:latest .
docker run -d --name noor-islam-bot \
  --env-file .env \
  --restart unless-stopped \
  -p 8080:8080 \
  -v noor-data:/app/data \
  noor-islam-bot:latest

docker logs -f noor-islam-bot
docker inspect --format '{{.State.Health.Status}}' noor-islam-bot
```

### 3.2 بـdocker compose

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100
docker compose exec bot python scripts/healthcheck.py --offline
docker compose down          # الإيقاف (يبقي البيانات)
```

`docker-compose.yml` يقرأ `env_file: .env`، ويعيد التشغيل تلقائياً
(`restart: unless-stopped`)، ويربط `./data` بـ`/app/data`.

### 3.3 تنبيه مهم حول التخزين

- **الكاش والصوت لا يُحفظان داخل الصورة.** الصورة تحتوي الكود و`app/data/*.json`
  فقط؛ أمّا `data/cache/hadith/` و`data/noor.db` فيُكتبان في **مجلّد عام (volume)**
  هو `./data` مضافاً إلى `/app/data`. بلا هذا الربط يضيع الكاش وقاعدة البيانات مع كل
  إعادة بناء للحاوية (تُعاد تنزيل كتب الحديث وقد تفقد تفضيلات المستخدمين).
- ملف `.dockerignore` يستثني `data/cache/` قصداً حتى لا تتضخّم الصورة بملفات تُعاد
  تنزيلها تلقائياً؛ إن أردت صورة تعمل بلا إنترنت من أول تشغيل فاحذف هذا الاستثناء
  وابنِ الصورة بعد تنزيل الكتب.
- في الإنتاج استعمل حجمًا مُسمّى لضمان البقاء: `-v noor-data:/app/data` في `docker run`،
  أو `volumes: [ "noor-data:/app/data" ]` في compose (بدلاً من الربط النسبي `./data`).

---

## 4. المسار الثالث: المنصّات المجانية / Serverless

(Vercel/Netlify لا تصلح لبوت تيليجرام طويل الأمد؛ المناسب: Render، Railway، Fly.io،
Deta، أو أي خدمة تشغيل حاويات.) في هذا المستودع: `Procfile` (`web: python -m app.main`)
و`render.yaml` جاهزان.

### 4.1 Render

1. اربط المستودع واختر **Blueprint** (يقرأ `render.yaml`).
2. في لوحة Render، املأ المتغيّرات السرّية (`BOT_TOKEN`, `WEBHOOK_SECRET` — النوع
   `sync: false` في الملف يعني «يُملأ من اللوحة ولا يُخزَّن في Git»).
3. Render يضبط `PORT` تلقائياً؛ اترك `RUN_MODE=webhook` و`WEBHOOK_BASE_URL` على
   `https://<اسم-الخدمة>.onrender.com`.
4. بعد أول نشر: `python scripts/setup_webhook.py --info` من جهازك (بعد ضبط `.env`
   محلياً على العنوان الجديد) للتأكد من تسجيل العنوان.

### 4.2 Railway

- مشروع جديد من GitHub، أمر التشغيل يُقرأ من `Procfile`، أو اضبطه يدوياً:
  `python -m app.main`.
- أضف المتغيّرات من لوحة Variables، وفعّل **Volume** على `/app/data` لبقاء الكاش
  وقاعدة البيانات.
- Railway يوفّر نطاقاً عاماً HTTPS؛ استعمله في `WEBHOOK_BASE_URL`.

### 4.3 Fly.io

```bash
fly launch --no-deploy                     # يكتشف Dockerfile
fly volumes create noor_data --size 1      # قرص دائم للكاش وقاعدة البيانات
fly secrets set BOT_TOKEN=... WEBHOOK_SECRET=...
fly deploy
fly logs
```

مع إعداد قرص في `fly.toml`:

```toml
[mounts]
  source = "noor_data"
  destination = "/app/data"
```

### 4.4 قيود لازمة الفهم

- **الفحص والاستيقاظ (Cold start)**: الخطط المجانية تُوقف الخدمة عند عدم الاستعمال.
  في وضع webhook، تُوقظ المنصّة الخدمة عند وصول التحديث، وقد يتأخّر أوّل ردّ بضع
  ثوانٍ، وتليجرام تُعيد إرسال التحديث إذا تأخّر الجواب كثيراً (لذلك المهلة الافتراضية
  في `app/llm.py` ٩٠ ثانية، واحرص على أن يكون العنوان HTTPS ويردّ 200 سريعاً).
- **`WEBHOOK_BASE_URL` و`WEBHOOK_SECRET` إلزاميان هنا**: بلا عنوان صحيح لا يصل شيء،
  وبلا سرّ صار ترويسة الأمان بلا معنى.
- **وضع webhook لا يُغني عن عملية دائمة للإشعارات**: قد تكفي الخدمة القابلة
  للاستيقاظ للرّد على الرسائل، لكن **الإشعارات المجدولة (`DAILY_PUSH`) تتطلّب عملية
  دائمة تعمل بلا انقطاع — حتى في وضع webhook** (مجدوماً cron/worker) أو مُشغّلاً
  خارجياً يستدعي نقطة داخلية في الأوقات المحدّدة؛ لأن الخدمة المُوقَفة لا تنفّذ
  مؤقّتاتها. البديل: اجعل `DAILY_PUSH=false` في المنصّات المجانية، أو شغّل مُشغِّلاً
  مستقلاً (`cron`/`systemd timer`/Render Cron Job) ينبّه الخدمة.
- **قاعدة البيانات والقصد**: نظام ملفات معظم المنصّات المجانية زائل (ephemeral)؛ بلا
  قرص دائم ستفقد التفضيلات وعدّاد السبحة مع كل إعادة نشر.
- القرص الدائم في Render/Railway متاح عادةً في الخطط المدفوعة؛ راجع حدود خطتك الحالية
  قبل الاعتماد عليه، وإلّا فاستعمل قاعدة بيانات خارجية أو تقبّل فقدان التفضيلات.

---

## 5. الوضع الحاليّ للبوت الأصلي — الترحيل من خدمة خارجية (نقل بلا انقطاع ولا تسريب)

**النسخة الحالية في هذا المستودع جاهزة للتشغيل مباشرة** بـ`python -m app.main`، ولا
تحتاج أي ترحيل تقني. أمّا إن كان للمالك **بوت قائم** يعمل من خدمة أو خادم خارجي
بالتوكن نفسه، فإن نقل العمل إلى هذه النسخة **قرار مالك** لا يُتّخذ تلقائياً: تشغيل
نسختين بالتوكن نفسه يُفسد استقبال التحديثات (polling وwebhook لا يتعايشان بشكل سليم).

الترتيب الآمن:

**الخطوة ١ — احتفظ بنسخة من الإعدادات الحالية.**
انسخ `.env` الحالي (أو متغيّرات بيئة الخدمة السابقة) إلى مكان آمن خارج Git، وسجّل:
عنوان الـwebhook الحالي، و`WEBHOOK_PATH`، وطريقة التشغيل (polling/webhook)، والمدينة
والتفضيلات الافتراضية. ولا تنسخ `data/noor.db` إن أردت الاحتفاظ بتفضيلات المستخدمين:

```bash
cp .env .env.backup.$(date +%F)
sqlite3 data/noor.db ".backup 'data/noor.db.$(date +%F).bak'"
ls -l .env.backup.* data/*.bak
```

**الخطوة ٢ — انقل العنوان.**
إذا كان البوت القديم في وضع webhook وستستعمل وضع webhook على العنوان الجديد:

```bash
# على الخادم الجديد، بعد ضبط .env على العنوان الجديد
set -a && . ./.env && set +a

curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WEBHOOK_BASE_URL}${WEBHOOK_PATH}" \
  --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
  --data-urlencode "drop_pending_updates=true" \
  -H "Content-Type: application/x-www-form-urlencoded" | python -m json.tool
```

وإذا أردت العودة إلى polling (وهو ما يلزم إن كان الخادم السابق يعمل بـpolling مع
نسخة ثانية، لأن polling وwebhook لا يعملان معاً بشكل سليم):

```bash
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" \
  | python -m json.tool
```

أو باختصار عبر سكربت المشروع:

```bash
python scripts/setup_webhook.py             # setWebhook (webhook)
python scripts/setup_webhook.py --delete    # deleteWebhook (polling)
```

**الخطوة ٣ — تحقّق.**

```bash
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" | python -m json.tool
python scripts/setup_webhook.py --info
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/getMe" | python -m json.tool
```

تحقّق من الحقول: `url` = العنوان الجديد، `has_custom_certificate=false`،
`pending_update_count` قريب من الصفر، و`last_error_date/last_error_message` غائبان.
ثم أرسل رسالة فعلية إلى البوت (مثلاً `مواقيت الصلاة`) للتأكد من وصول الردّ.

**الخطوة ٤ — اسحب التوكن القديم من أي خدمة سابقة.**
أوقف الخدمة القديمة تماماً (`systemctl stop` أو إيقاف الحاوية/المنصّة)، واحذف
`BOT_TOKEN` من متغيّرات بيئتها، واحذف `.env` القديم من ذلك الخادم. والأهم: إن كان
الخادم القديم سيُعاد استخدامه أو بيعه، امسح النسخ الاحتياطية التي تحتوي التوكن
(`shred -u .env.backup.*`). لا تُشغّل نسختين بالتوكن نفسه إلا إذا كانت إحداهما polls
والأخرى webhook — وهو وضع هشّ يُفضَّل تجنّبه.

**الخطوة ٥ — إعادة تعيين التوكن عند أدنى شكّ في تسريبه.**
كل تسريب (لقطة شاشة، مستودع عام، سجلّ CI، رسالة، ملف نسخة احتياطية، حاوية مهجورة)
يستوجب الإبطال الفوري:

1. افتح محادثة @BotFather ⇒ `/mybots` ⇒ اختر البوت ⇒ **API Token** ⇒ **Revoke current
   token** (أو `/revoke` ثم اختر البوت).
2. ضع التوكن الجديد في `.env` على الخادم فقط (`chmod 600 .env`).
3. إن كنت في وضع webhook: أعد `setWebhook` بالتوكن الجديد وأبقِ السرّ نفسه أو جدّده:

```bash
set -a && . ./.env && set +a
python scripts/setup_webhook.py
python scripts/setup_webhook.py --info
```

4. أعد تشغيل الخدمة: `sudo systemctl restart noor-islam-bot` (أو `docker compose up -d`).
5. راجع المستودع والمستودعات المتشعّبة بحثاً عن التوكن:
   `git log -p --all -- .env` و`grep -r "BOT_TOKEN" .` — وإن وُجد في تاريخ Git فالنصّ
   محروق أبداً ويجب إبطاله حتى لو حُذف في Commit لاحق.

> **حماية إضافية**: لا تضع `BOT_TOKEN` في `render.yaml` ولا في `Dockerfile` ولا في
> `docker-compose.yml` (المستودع لا يحتويهما كذلك). اجعله دائماً في `.env` أو في
> إعدادات المنصّة السرّية. وسكربت `scripts/setup_webhook.py` **لا يطبع التوكن إطلاقاً**
> ولا يُدرجه في سجلّ.

---

## 6. قائمة تحقّق سريعة بعد النشر

- [ ] `python scripts/healthcheck.py` يُرجِع رمز الخروج صفراً (ملفات البيانات + الاستيراد
      + الذاكرة، و`getMe` إن ضُبط التوكن).
- [ ] `python -m pytest -q` و`python scripts/smoke.py --offline` و`python scripts/smoke.py`
      ناجحة قبل النشر (القسم ٠).
- [ ] `python scripts/setup_webhook.py --info` (في webhook) يُظهر العنوان الصحيح بلا أخطاء.
- [ ] رسالة فعلية إلى البوت تُنتج ردّاً عربياً مع مصدره.
- [ ] `data/` قابل للكتابة والدائم (volume) و`DB_PATH` داخله.
- [ ] `.env` بصلاحيات `600` وغير مُتتبَّع في Git (`git check-ignore -v .env`).
- [ ] `DAILY_PUSH` مضبوط بما يوافق وجود عملية دائمة أو عدمه.
