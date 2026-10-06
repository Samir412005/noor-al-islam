"""الفحوصات التي يجريها الحارس على بوت نور الإسلام.

كل فحص يُعيد `CheckResult` ويقترح مُصلحاً عند الإمكان. الفحوص تعتمد على مكتبة
بايثون القياسية وحدها حيث أمكن، حتى يعمل الحارس حتى لو تعطّلت بيئة البوت.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import GuardianConfig

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    severity: str = WARNING
    fix: str | None = None
    meta: dict = field(default_factory=dict)


# ── أدوات مساعدة ───────────────────────────────────────────────────
def _run(args: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, str(exc)


def _http_json(url: str, timeout: float = 12.0, payload: dict | None = None,
               headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "ignore")
            try:
                return response.status, json.loads(body or "{}")
            except ValueError:
                return response.status, {}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "ignore") or "{}")
        except Exception:
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001 - أي فشل شبكي = فحص فاشل
        return 0, {"_error": type(exc).__name__}


def _pid_cmdlines() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="ignore")
        except OSError:
            continue
        out.append((int(pid), cmd.replace("\x00", " ").strip()))
    return out


# ── الفحوصات ───────────────────────────────────────────────────────
WARMUP_GRACE_SECONDS = 30


def _worker_ages() -> list[int]:
    """أعمار عمليات البوت (ثوانٍ) — لت distinguishable الإقلاع من العطل."""
    ages: list[int] = []
    for pid, cmd in _pid_cmdlines():
        if "app.main" not in cmd:
            continue
        code, out = _run(["ps", "-o", "etimes=", "-p", str(pid)], timeout=10)
        value = out.strip().split()[0] if out.strip() else ""
        if code == 0 and value.isdigit():
            ages.append(int(value))
    return ages


def is_warming_up() -> bool:
    """هل البوت في نافذة إقلاعه الطبيعية؟ (لا نُصلح إقلاعاً سليماً)"""
    ages = _worker_ages()
    return bool(ages) and min(ages) < WARMUP_GRACE_SECONDS


def check_supervisor(cfg: GuardianConfig) -> CheckResult:
    pids = [p for p, cmd in _pid_cmdlines() if "run_supervisor" in cmd]
    if pids:
        return CheckResult("supervisor", True, f"المشرف يعمل ({len(pids)} عملية)")
    return CheckResult(
        "supervisor", False, "المشرف غير مشغّل — لن يُعاد تشغيل البوت عند سقوطه",
        severity=CRITICAL, fix="restart_supervisor",
    )


def check_keepalive(cfg: GuardianConfig) -> CheckResult:
    pids = [p for p, cmd in _pid_cmdlines() if "keepalive.sh" in cmd]
    if pids:
        return CheckResult("keepalive", True, "الحراسة الدقيقة تعمل")
    return CheckResult(
        "keepalive", False, "الحراسة الدقيقة غير مشغّلة",
        severity=WARNING, fix="restart_keepalive",
    )


def check_workers(cfg: GuardianConfig) -> CheckResult:  # noqa: C901
    code, out = _run(["ss", "-ltnp"])
    serving = [line for line in out.splitlines() if f":{cfg.port}" in line]
    target_env = cfg.target_env
    reuse = target_env.get("REUSE_PORT", "").lower() in {"1", "true", "yes", "on"}
    if target_env.get("RUN_MODE", "webhook") == "polling":
        expected = 1
    else:
        expected = 2 if reuse else 1
    if len(serving) < expected and is_warming_up():
        return CheckResult(
            "workers", True,
            f"قيد الإقلاع ({len(serving)}/{expected}) — نافذة سماح {WARMUP_GRACE_SECONDS}ث",
            severity=INFO, meta={"warming_up": True},
        )
    if len(serving) > expected + 1:
        return CheckResult(
            "workers", False,
            f"عمليات زائدة: {len(serving)} تخدم (المتوقّع {expected}) — استهلاك ذاكرة مضاعف",
            severity=WARNING, fix="full_restart", meta={"serving": len(serving)},
        )
    if len(serving) >= expected:
        return CheckResult("workers", True, f"{len(serving)} عمليات تخدم على {cfg.port}",
                           meta={"serving": len(serving)})
    return CheckResult(
        "workers", False,
        f"عمليات الخدمة {len(serving)}/{expected} على {cfg.port}",
        severity=CRITICAL, fix="full_restart" if len(serving) == 0 else "restart_supervisor",
        meta={"serving": len(serving)},
    )


def check_local_health(cfg: GuardianConfig) -> CheckResult:
    status, data = _http_json(f"http://127.0.0.1:{cfg.port}/health", timeout=8)
    if status == 200 and data.get("status") == "ok":
        return CheckResult("local_health", True, "نقطة الفحص المحلية تستجيب", meta=data)
    if is_warming_up():
        return CheckResult("local_health", True, "قيد الإقلاع — لم يستمع بعد",
                           severity=INFO, meta={"warming_up": True})
    return CheckResult(
        "local_health", False, f"نقطة الفحص المحلية لا تستجيب (HTTP {status})",
        severity=CRITICAL, fix="full_restart",
    )


def check_public_health(cfg: GuardianConfig) -> CheckResult:
    """الأهمّ: هل البوت منشور فعلاً على الإنترنت؟ (لا يكفي أن يعمل محلياً)."""
    if not cfg.base_url:
        return CheckResult("public_health", False, "WEBHOOK_BASE_URL غير مضبوط",
                           severity=CRITICAL, fix="sync_base_url")
    status, data = _http_json(f"{cfg.base_url}/health", timeout=15)
    if status == 200 and data.get("status") == "ok":
        return CheckResult("public_health", True, "الوصول من الإنترنت يعمل", meta=data)
    if is_warming_up():
        return CheckResult("public_health", True, "قيد الإقلاع — العنوان العام لم يستجب بعد",
                           severity=INFO, meta={"warming_up": True})
    return CheckResult(
        "public_health", False, f"العنوان العام لا يستجيب (HTTP {status})",
        severity=CRITICAL, fix="sync_base_url",
    )


def check_telegram(cfg: GuardianConfig) -> CheckResult:
    if not cfg.bot_token:
        return CheckResult("telegram", False, "لا يوجد توكن للبوت الهدف", severity=CRITICAL)
    status, data = _http_json(
        f"https://api.telegram.org/bot{cfg.bot_token}/getWebhookInfo", timeout=20
    )
    if status != 200 or not data.get("ok"):
        detail = (data.get("description") or f"HTTP {status}")[:120]
        severity = CRITICAL if status in (401, 404) else WARNING
        return CheckResult(
            "telegram", False, f"تيليجرام لا يستجيب: {detail}",
            severity=severity, fix="check_token" if status in (401, 404) else None,
        )
    info = data["result"]
    expected = f"{cfg.base_url}{cfg.webhook_path}"
    pending = int(info.get("pending_update_count") or 0)
    error = info.get("last_error_message") or ""

    if (info.get("url") or "") != expected:
        return CheckResult(
            "telegram", False,
            f"الويب هوك غير مطابق ({(info.get('url') or 'فارغ')[:50]}) — الرسائل لا تصل!",
            severity=CRITICAL, fix="assert_webhook",
        )
    if pending > cfg.pending_limit:
        return CheckResult(
            "telegram", False, f"تحديثات منتظرة كثيرة: {pending} — المعالجة متأخّرة",
            severity=WARNING, fix="full_restart", meta={"pending": pending},
        )
    if error and "502" not in error:
        return CheckResult(
            "telegram", False, f"آخر خطأ من تيليجرام: {error[:110]}",
            severity=WARNING, meta={"last_error": error},
        )
    note = "الويب هوك مضبوط"
    if error:
        note += " (آخر خطأ قديم: 502 عند إقلاع سابق)"
    return CheckResult("telegram", True, note, meta={"pending": pending})


def check_pipeline(cfg: GuardianConfig) -> CheckResult:
    """اختبار حقيقي للمسار الكامل: يحاكي تحديث تيليجرام ويرى هل يُقبل.

    هذا أقوى فحص: يكشف تعطّل المعالجات أو انقطاع المسار بين الإنترنت والبوت،
    حتى لو كانت نقطة الفحص الصحّي تعمل.
    """
    if not (cfg.base_url and cfg.webhook_secret):
        return CheckResult("pipeline", False, "لا يمكن الفحص بلا رابط/سرّ",
                           severity=WARNING)
    import time

    probe_chat = 424242
    update = {
        "update_id": int(time.time() * 1000) % 2_000_000_000,
        "message": {
            "message_id": 1,
            "from": {"id": probe_chat, "is_bot": False, "first_name": "الحارس"},
            "chat": {"id": probe_chat, "type": "private", "first_name": "الحارس"},
            "date": int(time.time()),
            # أمر داخلي صامت: يمرّ بكل الطبقات ولا يُرسل أي رسالة (بلا ضجيج)
            "text": "/__guardian_probe",
            "entities": [{"offset": 0, "length": 17, "type": "bot_command"}],
        },
    }
    status, _ = _http_json(
        f"{cfg.base_url}{cfg.webhook_path}",
        timeout=20,
        payload=update,
        headers={
            "Content-Type": "application/json",
            "X-Telegram-Bot-Api-Secret-Token": cfg.webhook_secret,
        },
    )
    _clean_probe_trace(cfg, probe_chat)
    if status == 200:
        return CheckResult("pipeline", True, "المسار الكامل يقبل التحديثات (HTTP 200)")
    if is_warming_up():
        return CheckResult("pipeline", True, "قيد الإقلاع — لم يُقبل الفحص بعد",
                           severity=INFO, meta={"warming_up": True})
    if status == 401:
        return CheckResult(
            "pipeline", False, "الويب هوك يرفض السرّ — WEBHOOK_SECRET غير مطابق",
            severity=CRITICAL, fix="assert_webhook",
        )
    return CheckResult(
        "pipeline", False, f"المسار الكامل لا يستجيب (HTTP {status})",
        severity=CRITICAL, fix="full_restart",
    )


def _clean_probe_trace(cfg: GuardianConfig, chat_id: int) -> None:
    """ينظّف أثر فحص المسار من قاعدة البيانات (لا نُلوّث بيانات المالك)."""
    db_path = cfg.target_dir / cfg.target_env.get("DB_PATH", "data/noor.db")
    if not db_path.exists():
        return
    try:
        con = sqlite3.connect(str(db_path), timeout=5)
        for table in ("users", "history", "counters", "push_state"):
            try:
                con.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
            except sqlite3.Error:
                continue
        con.commit()
        con.close()
    except sqlite3.Error:
        return


def check_data_files(cfg: GuardianConfig) -> CheckResult:
    data_dir = cfg.target_dir / "app" / "data"
    problems: list[str] = []
    details: list[str] = []
    for name, minimum in (("quran_uthmani.json", 100), ("adhkar.json", 50), ("occasions.json", 3)):
        path = data_dir / name
        if not path.exists():
            problems.append(f"{name} مفقود")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{name} تالف ({type(exc).__name__})")
            continue
        if name.startswith("quran"):
            count = len(payload.get("surahs") or [])
        elif name.startswith("adhkar"):
            count = len(payload.get("categories") or [])
        else:
            count = len(payload if isinstance(payload, list) else payload.get("occasions", []))
        details.append(f"{name}: {count}")
        if count < minimum:
            problems.append(f"{name} ناقص ({count})")
    if problems:
        return CheckResult(
            "data_files", False, " · ".join(problems),
            severity=WARNING, fix="refetch_data",
        )
    return CheckResult("data_files", True, " · ".join(details))


def check_disk(cfg: GuardianConfig) -> CheckResult:
    total, used, free = shutil.disk_usage(str(cfg.target_dir))
    free_mb = free // (1024 * 1024)
    logs_dir = cfg.target_dir / "logs"
    log_mb = 0
    if logs_dir.exists():
        log_mb = sum(f.stat().st_size for f in logs_dir.glob("*.log")) // (1024 * 1024)
    detail = f"حرّ: {free_mb} م.ب · سجلّات: {log_mb} م.ب"
    if free_mb < cfg.disk_min_free_mb:
        return CheckResult("disk", False, f"المساحة الحرّة منخفضة — {detail}",
                           severity=CRITICAL, fix="trim_logs")
    if log_mb > 200:
        return CheckResult("disk", False, f"السجلّات متضخّمة — {detail}",
                           severity=WARNING, fix="trim_logs")
    return CheckResult("disk", True, detail)


def check_memory(cfg: GuardianConfig) -> CheckResult:
    worst = 0
    worst_pid = 0
    total_mb = 0
    for pid, cmd in _pid_cmdlines():
        if "app.main" not in cmd:
            continue
        try:
            rss_kb = 0
            for line in open(f"/proc/{pid}/status"):
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
            rss_mb = rss_kb // 1024
        except (OSError, ValueError, IndexError):
            continue
        total_mb += rss_mb
        if rss_mb > worst:
            worst, worst_pid = rss_mb, pid
    if worst == 0:
        return CheckResult("memory", False, "لم أجد عمليات البوت لقياس الذاكرة",
                           severity=CRITICAL, fix="full_restart")
    detail = f"أثقل عملية: {worst} م.ب (pid {worst_pid}) · الإجمالي: {total_mb} م.ب"
    if worst > cfg.rss_limit_mb:
        return CheckResult("memory", False, f"استهلاك مرتفع — {detail}",
                           severity=WARNING, fix="restart_workers")
    return CheckResult("memory", True, detail, meta={"worst_mb": worst})


def check_database(cfg: GuardianConfig) -> CheckResult:
    db_path = cfg.target_dir / cfg.target_env.get("DB_PATH", "data/noor.db")
    if not db_path.exists():
        return CheckResult("database", True, "قاعدة البيانات تُنشأ عند أول استخدام")
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        verdict = con.execute("PRAGMA quick_check").fetchone()[0]
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        con.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("database", False, f"قاعدة البيانات غير قابلة للقراءة: {type(exc).__name__}",
                           severity=CRITICAL, fix="repair_database")
    if verdict != "ok":
        return CheckResult("database", False, f"قاعدة البيانات تالفة: {verdict[:80]}",
                           severity=CRITICAL, fix="repair_database")
    return CheckResult("database", True, f"سليمة · مستخدمون: {users}")


def check_llm(cfg: GuardianConfig) -> CheckResult:
    """يتأكّد أن مزوّد الذكاء (إن كان مفعّلاً) يردّ فعلاً."""
    base = cfg.target_env.get("LLM_BASE_URL", "")
    model = cfg.target_env.get("LLM_MODEL", "")
    key = cfg.target_env.get("LLM_API_KEY", "")
    if not (base and model):
        return CheckResult("llm", True, "غير مفعّل (وضع البحث المباشر)")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    status, data = _http_json(
        f"{base.rstrip('/')}/chat/completions",
        timeout=45,
        payload={"model": model, "messages": [{"role": "user", "content": "قل: جاهز"}], "max_tokens": 8},
        headers=headers,
    )
    if status == 200 and (data.get("choices") or []):
        return CheckResult("llm", True, f"النموذج {model} يردّ")
    detail = (data.get("error") or {}).get("message") or (data.get("description") or f"HTTP {status}")
    return CheckResult(
        "llm", False, f"مزوّد الذكاء لا يردّ: {str(detail)[:110]}",
        severity=WARNING, fix="disable_llm_on_failure",
    )


def check_guardian_bot(cfg: GuardianConfig) -> CheckResult:
    if not cfg.guardian_bot_token:
        return CheckResult(
            "guardian_bot", True,
            "بوت الحارس غير مُفعّل (يحتاج توكن من @BotFather — خطوة يدوية واحدة)",
            severity=INFO,
        )
    status, data = _http_json(
        f"https://api.telegram.org/bot{cfg.guardian_bot_token}/getMe", timeout=20
    )
    if status != 200 or not data.get("ok"):
        return CheckResult(
            "guardian_bot", False, f"توكن بوت الحارس غير صالح (HTTP {status})",
            severity=WARNING, fix="check_token",
        )
    username = data["result"].get("username")

    # التوكن صالح — لكن هل العملية نفسها تعمل؟ (بوت متوقّف = تنبيهات صامتة)
    running = any("guardian.guardian_bot" in cmd for _pid, cmd in _pid_cmdlines())
    if not running:
        return CheckResult(
            "guardian_bot", False,
            f"توكن @{username} صالح لكن بوت الحارس **متوقّف** — لن تصلك تنبيهاته",
            severity=WARNING, fix="restart_guardian_bot",
        )
    return CheckResult("guardian_bot", True, f"بوت الحارس يعمل: @{username} (polling)")


def run_all(cfg: GuardianConfig, *, include_pipeline: bool | None = None) -> list[CheckResult]:
    probe = cfg.pipeline_probe if include_pipeline is None else include_pipeline
    checks = [
        check_supervisor,
        check_keepalive,
        check_workers,
        check_local_health,
        check_public_health,
        check_telegram,
        check_pipeline if probe else None,
        check_data_files,
        check_disk,
        check_memory,
        check_database,
        check_llm,
        check_guardian_bot,
    ]
    results: list[CheckResult] = []
    for check in checks:
        if check is None:
            continue
        try:
            results.append(check(cfg))
        except Exception as exc:  # noqa: BLE001 - فحص لا يجب أن يُسقط الحارس
            results.append(
                CheckResult(check.__name__, False, f"فشل الفحص نفسه: {type(exc).__name__}",
                            severity=WARNING)
            )
    return results


def summarize(results: list[CheckResult]) -> dict:
    critical = [r for r in results if not r.ok and r.severity == CRITICAL]
    warnings = [r for r in results if not r.ok and r.severity != CRITICAL]
    return {
        "healthy": not critical and not warnings,
        "critical": len(critical),
        "warnings": len(warnings),
        "failed": [r.name for r in critical + warnings],
    }
