#!/usr/bin/env bash
# تقرير حالة البوت: العمليات، المنفذ، الويب هوك، آخر أخطاء، وإحصاءات الأداء.
set -u
cd "$(dirname "$0")/.." || exit 1

python3 - <<'PY'
import json, subprocess, urllib.request
from pathlib import Path

ROOT = Path.cwd()


def load_env() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


env = load_env()
print("── العمليات ──")
out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
serving = [l for l in out.splitlines() if ":8080" in l]
print(f"عمليات تخدم على المنفذ 8080: {len(serving)}")
for line in serving:
    pid = line.split("pid=")[1].split(",")[0] if "pid=" in line else "?"
    role = ""
    for name in ("primary", "secondary", "tertiary"):
        log = ROOT / "logs" / f"bot-{name}.log"
        if log.exists() and pid in log.read_text(errors="ignore")[-4000:]:
            role = name
    print(f"   pid={pid} {role}")

print("\n── الصحة (محلياً) ──")
try:
    with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=8) as r:
        data = json.load(r)
    print(json.dumps(data, ensure_ascii=False, indent=2))
except Exception as exc:
    print(f"✗ تعذّر الفحص المحلي: {exc}")

base = env.get("WEBHOOK_BASE_URL", "").rstrip("/")
if base:
    print(f"\n── الصحة (من الإنترنت) ──  {base}/health")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=12) as r:
            print(json.dumps(json.load(r), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"✗ العنوان العام لا يستجيب: {exc}")

token = env.get("BOT_TOKEN", "")
if token:
    print("\n── تيليجرام ──")
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=15
        ) as r:
            info = json.load(r)["result"]
        expected = f"{base}{env.get('WEBHOOK_PATH', '/telegram/webhook')}"
        print(f"الويب هوك: {'✅ مطابق' if info.get('url') == expected else '⚠️ غير مطابق: ' + (info.get('url') or 'فارغ')}")
        print(f"تحديثات منتظرة: {info.get('pending_update_count')}")
        error = info.get("last_error_message")
        print(f"آخر خطأ: {error or 'لا شيء ✅'}")
    except Exception as exc:
        print(f"✗ تعذّر فحص تيليجرام: {exc}")

print("\n── آخر سطور السجلّ ──")
for name in ("primary", "secondary", "tertiary"):
    log = ROOT / "logs" / f"bot-{name}.log"
    if log.exists():
        tail = [l for l in log.read_text(errors="ignore").splitlines() if l.strip()][-3:]
        print(f"[{name}]")
        for line in tail:
            print("   " + line[:140])
PY
