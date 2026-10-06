"""المُصلِحات — كل خلل له إجراء إصلاح محدّد وآمن (idempotent).

قواعد التصميم:
1. لا إصلاح بلا ميزانية (`state.can_fix`) — منعاً لحلقات إصلاح لا تنتهي.
2. الإصلاحات لا تُسقط الخدمة: نُعيد تشغيل عملية واحدة أو المشرف، لا كل شيء.
3. كل إصلاح يُسجَّل (نجاح/فشل) في سجلّ الحوادث.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from .config import GuardianConfig


def log(cfg: GuardianConfig, message: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    with cfg.log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"[fix] {stamp} — {message}\n")


def _spawn(cfg: GuardianConfig, args: list[str]) -> bool:
    try:
        subprocess.Popen(
            args,
            cwd=str(cfg.target_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError as exc:  # pragma: no cover
        log(cfg, f"تعذّر التشغيل {args}: {exc}")
        return False


def _pids_matching(needle: str) -> list[int]:
    found: list[int] = []
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
        except OSError:
            continue
        if needle in cmd:
            found.append(int(pid))
    return found


def _terminate(pids: list[int]) -> int:
    killed = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except OSError:
            pass
    return killed


# ── الإصلاحات ──────────────────────────────────────────────────────
def restart_supervisor(cfg: GuardianConfig) -> str:
    if _pids_matching("run_supervisor"):
        return "المشرف يعمل أصلاً"
    ok = _spawn(cfg, ["setsid", "nohup", "bash", "scripts/run_supervisor.sh"])
    return "أُعيد تشغيل المشرف" if ok else "تعذّر تشغيل المشرف"


def restart_keepalive(cfg: GuardianConfig) -> str:
    if _pids_matching("keepalive.sh"):
        return "الحراسة الدقيقة تعمل أصلاً"
    keepalive = cfg.target_dir / "scripts" / "keepalive.sh"
    if not keepalive.exists():
        return "لا يوجد سكربت حراسة دقيقة"
    ok = _spawn(cfg, ["setsid", "nohup", "bash", "scripts/keepalive.sh"])
    return "أُعيد تشغيل الحراسة الدقيقة" if ok else "تعذّر تشغيل الحراسة"


def full_restart(cfg: GuardianConfig) -> str:
    """المطرقة الكبرى: إعادة تشغيل نظيفة لكل شيء (بلا إسقاط تحديثات)."""
    killed = _terminate(_pids_matching("app.main"))
    _terminate(_pids_matching("run_supervisor"))
    _terminate(_pids_matching("keepalive.sh"))
    time.sleep(1)
    restart_supervisor(cfg)
    restart_keepalive(cfg)
    return f"إعادة تشغيل كاملة (أُوقفت {killed} عملية)"


def restart_workers(cfg: GuardianConfig) -> str:
    """يعيد العمليات الثقيلة واحدة واحدة؛ المشرف يعيدها فوراً بلا انقطاع."""
    pids = _pids_matching("app.main")
    if len(pids) > 1:
        pids = pids[: len(pids) - 1]  # نُبقي واحدة تخدم
    killed = _terminate(pids)
    return f"أُعيد تشغيل {killed} عملية (والباقي يخدم بلا انقطاع)"


def assert_webhook(cfg: GuardianConfig) -> str:
    """يعيد ضبط الويب هوك على العنوان الصحيح (يُصلح حالات «البوت صامت»)."""
    if not (cfg.bot_token and cfg.base_url):
        return "لا يمكن ضبط الويب هوك (توكن/عنوان ناقص)"
    payload = {
        "url": f"{cfg.base_url}{cfg.webhook_path}",
        "drop_pending_updates": False,
        "max_connections": 40,
    }
    if cfg.webhook_secret:
        payload["secret_token"] = cfg.webhook_secret
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg.bot_token}/setWebhook",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode() or "{}")
        return "أُعيد ضبط الويب هوك ✅" if data.get("ok") else f"رفض تيليجرام: {data.get('description')}"
    except Exception as exc:  # noqa: BLE001
        return f"تعذّر ضبط الويب هوك: {type(exc).__name__}"


def sync_base_url(cfg: GuardianConfig) -> str:
    """يزامن WEBHOOK_BASE_URL مع العنوان الفعلي للساندبوكس ثم يعيد ضبط الويب هوك."""
    sandbox_id = (os.environ.get("E2B_SANDBOX_ID") or "").strip()
    if not sandbox_id:
        return assert_webhook(cfg)
    actual = f"https://{cfg.port}-{sandbox_id}.e2b.dev"
    if actual == cfg.base_url:
        return assert_webhook(cfg)

    env_path = cfg.target_dir / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    import re

    if re.search(r"^WEBHOOK_BASE_URL=.*$", text, re.MULTILINE):
        text = re.sub(r"^WEBHOOK_BASE_URL=.*$", f"WEBHOOK_BASE_URL={actual}", text, flags=re.MULTILINE)
    else:
        text += f"\nWEBHOOK_BASE_URL={actual}\n"
    env_path.write_text(text, encoding="utf-8")
    os.environ["WEBHOOK_BASE_URL"] = actual
    result = assert_webhook(cfg)
    return f"حُدّث العنوان العام إلى {actual} · {result}"


def refetch_data(cfg: GuardianConfig) -> str:
    """يعيد توليد ملفات البيانات المُجمَّعة (القرآن/الأذكار) عند فقدانها أو تلفها."""
    outcomes: list[str] = []
    for script in ("scripts/fetch_quran.py", "scripts/fetch_adhkar.py"):
        path = cfg.target_dir / script
        if not path.exists():
            continue
        try:
            done = subprocess.run(
                ["python", script], cwd=str(cfg.target_dir),
                capture_output=True, text=True, timeout=420,
            )
            outcomes.append(f"{Path(script).name}: {'نجح' if done.returncode == 0 else 'فشل'}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            outcomes.append(f"{Path(script).name}: {type(exc).__name__}")
    return " · ".join(outcomes) or "لا سكربتات بيانات"


def trim_logs(cfg: GuardianConfig) -> str:
    logs = cfg.target_dir / "logs"
    if not logs.exists():
        return "لا مجلّد سجلّات"
    freed = 0
    for path in logs.glob("*.log"):
        try:
            size = path.stat().st_size
            if size > 2_000_000:
                tail = path.read_bytes()[-300_000:]
                path.write_bytes(tail)
                freed += size - len(tail)
        except OSError:
            continue
    # كاش الحديث القابل لإعادة التنزيل
    cache = cfg.target_dir / "data" / "cache"
    if cache.exists():
        for old in cache.rglob("*.tmp"):
            try:
                old.unlink()
            except OSError:
                pass
    return f"قُلّصت السجلّات ({freed // 1024} ك.ب)"


def repair_database(cfg: GuardianConfig) -> str:
    """يُزيح قاعدة بيانات تالفة جانباً لتُبنى من جديد (لا يُحذف شيء)."""
    db_path = cfg.target_dir / cfg.target_env.get("DB_PATH", "data/noor.db")
    if not db_path.exists():
        return "لا قاعدة بيانات"
    backup = db_path.with_suffix(f".corrupt-{int(time.time())}.db")
    try:
        shutil.move(str(db_path), str(backup))
        return f"أُزيحت القاعدة التالفة إلى {backup.name} (ستُبنى جديدة تلقائياً)"
    except OSError as exc:
        return f"تعذّر إصلاح القاعدة: {exc}"


def disable_llm_on_failure(cfg: GuardianConfig) -> str:
    """إن فشل مزوّد الذكاء، نُوقف الذكاء ليعود البوت للبحث المباشر بدل أن يتعطّل."""
    env_path = cfg.target_dir / ".env"
    if not env_path.exists():
        return "لا ملف إعدادات"
    import re

    text = env_path.read_text(encoding="utf-8")
    text = re.sub(r"^LLM_BASE_URL=.*$", "LLM_BASE_URL=", text, flags=re.MULTILINE)
    text = re.sub(r"^LLM_MODEL=.*$", "LLM_MODEL=", text, flags=re.MULTILINE)
    env_path.write_text(text, encoding="utf-8")
    restart_workers(cfg)
    return "أُوقف الذكاء مؤقتاً (تعذّر المزوّد) والبوت يعمل بالبحث المباشر"


FIXERS = {
    "restart_supervisor": restart_supervisor,
    "restart_keepalive": restart_keepalive,
    "full_restart": full_restart,
    "restart_workers": restart_workers,
    "assert_webhook": assert_webhook,
    "sync_base_url": sync_base_url,
    "refetch_data": refetch_data,
    "trim_logs": trim_logs,
    "repair_database": repair_database,
    "disable_llm_on_failure": disable_llm_on_failure,
}

#: إصلاحات لا تُنفَّذ تلقائياً (تحتاج تدخّل المالك أو يُنبَّه عليها فقط)
MANUAL_ONLY = {"check_token"}
