# ─────────────────────────────────────────────────────────────────────
# صورة بوت «نور الإسلام» (NoorIslamBot)
#   بناء:  docker build -t noor-islam-bot:latest .
#   تشغيل: docker run -d --env-file .env -v noor-data:/app/data \
#            --restart unless-stopped -p 8080:8080 noor-islam-bot:latest
#
# ملاحظة: قاعدة البيانات (data/noor.db) وكاش الحديث (data/cache/hadith/)
# لا يُخزَّنان داخل الصورة، بل في مجلّد عام (volume) يُربط بـ/app/data.
# ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# بيئة تشغيل نظيفة وثابتة
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Africa/Algiers

WORKDIR /app

# curl لأجل HEALTHCHECK فقط، ثم تُنظَّف قوائم الحزم
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# طبقة الاعتماديات أولاً حتى يستفيد البناء من الكاش
COPY requirements.txt ./
RUN pip install --quiet --no-cache-dir -r requirements.txt

# الكود والبيانات المُجمَّعة (app/data/*.json) والسكربتات
COPY app ./app
COPY scripts ./scripts

# مجلّد البيانات (قاعدة SQLite + كاش الحديث) ونقطة الربط للحجم العام
RUN mkdir -p /app/data \
    && useradd --create-home --no-log-init --uid 10001 noor \
    && chown -R noor:noor /app

USER noor

# الافتراضي webhook في الحاويات؛ غيّره إلى polling عبر RUN_MODE=polling
ENV RUN_MODE=webhook \
    PORT=8080

EXPOSE 8080

# فحص صحة بسيط: ملفات البيانات + الاستيراد + الذاكرة (بلا شبكة ولا توكن)
HEALTHCHECK --interval=60s --timeout=15s --start-period=20s --retries=3 \
    CMD python scripts/healthcheck.py --offline || exit 1

CMD ["python", "-m", "app.main"]
