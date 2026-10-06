"""حلقة الحارس: يفحص → يُصلح → يُنبّه → يُسجّل. خدمة مستقلّة عن بوت نور الإسلام.

    python -m guardian.daemon                # تشغيل مستمرّ
    python -m guardian.daemon --once         # دورة واحدة (للفحص اليدوي/cron)
    python -m guardian.daemon --status       # تقرير الحالة الآن
    python -m guardian.daemon --force-fix    # يفرض الإصلاحات متجاهلاً الميزانية
    python -m guardian.daemon --selftest     # يفحص الحارس نفسه (قنوات التنبيه)
"""
from __future__ import annotations

import argparse
import sys
import time

from . import alerting, checks, fixers
from .config import GuardianConfig, config
from .state import (
    can_alert,
    can_fix,
    format_status,
    load_state,
    mark_alert,
    mark_fix,
    recent_incidents,
    record_incident,
    save_state,
    write_heartbeat,
)


def _label(result) -> str:
    return f"{result.name}: {result.detail}"


def run_cycle(cfg: GuardianConfig, *, force_fix: bool = False, verbose: bool = True) -> dict:
    """دورة واحدة: قياس، إصلاح، تنبيه. تُعيد ملخّصاً."""
    state = load_state(cfg)
    write_heartbeat(cfg)

    results = checks.run_all(cfg)
    summary = checks.summarize(results)
    failed = [r for r in results if not r.ok]

    # ── الإصلاح ────────────────────────────────────────────────────
    applied: list[dict] = []
    for result in failed:
        action = result.fix
        if not action or action in fixers.MANUAL_ONLY:
            continue
        if not force_fix and not can_fix(state, result.name, cfg.fix_budget_per_hour):
            if verbose:
                print(f"… تجاوزنا ميزانية إصلاح «{result.name}» (منع حلقة إصلاح)")
            continue
        function = fixers.FIXERS.get(action)
        if function is None:
            continue
        try:
            outcome = function(cfg)
        except Exception as exc:  # noqa: BLE001 - إصلاح فاشل لا يُسقط الحارس
            outcome = f"فشل الإصلاح: {type(exc).__name__}"
        mark_fix(state, result.name)
        fixers.log(cfg, f"{result.name} → {outcome}")
        record_incident(
            cfg,
            {
                "name": result.name,
                "problem": result.detail,
                "action": action,
                "outcome": outcome,
                "severity": result.severity,
            },
        )
        applied.append({"name": result.name, "action": action, "outcome": outcome})
        if verbose:
            print(f"🔧 {result.name}: {outcome}")
        if not force_fix:
            time.sleep(1)  # نُعطي الإصلاح فرصة قبل التالي

    # ── إعادة القياس بعد الإصلاح (للتحقّق) ────────────────────────
    if applied:
        time.sleep(4)
        results = checks.run_all(cfg)
        summary = checks.summarize(results)
        failed = [r for r in results if not r.ok]

    # ── التنبيه ────────────────────────────────────────────────────
    previous = state.get("last_status") or {}
    was_healthy = bool(previous.get("healthy", True))
    is_healthy = summary["healthy"]

    if failed:
        severity = "critical" if summary["critical"] else "warning"
        key = f"{severity}:{','.join(sorted(r.name for r in failed))}"
        if can_alert(state, key, cfg.alert_cooldown):
            body = format_status(results, state, cfg)
            if applied:
                body += "\n\n🔧 الإصلاحات المنفّذة:\n" + "\n".join(
                    f"• {item['name']}: {item['outcome']}" for item in applied
                )
            channels = alerting.dispatch(cfg, "خلل في البوت", body, severity)
            mark_alert(state, key)
            fixers.log(cfg, f"تنبيه [{severity}] عبر {channels or 'لا قناة'}: {key}")
            if verbose:
                print(f"📣 تنبيه ({severity}) عبر: {channels or 'لا قناة متاحة'}")
    elif not was_healthy:
        # تعافٍ بعد عطل ⇒ طمأنة المالك
        body = format_status(results, state, cfg)
        channels = alerting.dispatch(cfg, "عاد البوت للعمل", body, "ok")
        fixers.log(cfg, f"تنبيه تعافٍ عبر {channels or 'لا قناة'}")
        if verbose:
            print(f"✅ تعافٍ — أُبلغ عبر: {channels or 'لا قناة متاحة'}")

    # ── تسجيل الحالة ──────────────────────────────────────────────
    state["last_status"] = {
        "healthy": is_healthy,
        "critical": summary["critical"],
        "warnings": summary["warnings"],
        "failed": summary["failed"],
        "ts": int(time.time()),
    }
    save_state(cfg, state)

    return {
        "summary": summary,
        "results": results,
        "applied": applied,
        "checked_at": int(time.time()),
    }


def selftest(cfg: GuardianConfig) -> int:
    """يتأكّد أن الحارس نفسه سليم: القنوات، الصلاحيات، والمسارات."""
    problems = 0
    print("── فحص الحارس نفسه ──")
    print(f"مجلّد الهدف: {cfg.target_dir} {'✅' if cfg.target_dir.exists() else '🛑'}")
    if not cfg.target_dir.exists():
        return 1

    env = cfg.target_env
    print(f"توكن البوت الهدف: {'✅' if env.get('BOT_TOKEN') else '🛑'}")
    print(f"العنوان العام: {cfg.base_url or '🛑 غير مضبوط'}")
    print(f"السرّ: {'✅' if cfg.webhook_secret else '🛑'}")
    print(f"قناة تيليجرام: {'مفعّلة' if cfg.notify_telegram else 'موقوفة'}")
    print(f"قناة التقويم: {'مفعّلة' if cfg.notify_calendar else 'موقوفة'}")
    print(f"بوت الحارس: {'مفعّل: ' + cfg.guardian_bot_token[:8] + '…' if cfg.guardian_bot_token else 'غير مفعّل (يحتاج توكن من BotFather)'}")

    if cfg.notify_calendar:
        payload = {
            "calendarId": cfg.calendar_id,
            "summary": "✅ نور الإسلام — اختبار قناة تنبيه الحارس",
            "description": "هذا اختبار لقناة تنبيه الحارس. إن وصلتك إشعاراً على هاتفك فالقناة تعمل.",
            "start": {"dateTime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()) + "+00:00"},
            "end": {"dateTime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 600)) + "+00:00"},
        }
        ok = alerting._connector(cfg, "google_calendar.events.create", payload)
        print(f"اختبار قناة التقويم: {'✅ نجح' if ok else '⚠️ فشل (سجّلت الخطأ)'}")
        problems += 0 if ok else 1

    if cfg.bot_token:
        ok = alerting.notify_telegram(
            cfg, "اختبار الحارس", "فحص ذاتي: قناة التنبيه عبر البوت تعمل ✅", "info"
        )
        print(f"اختبار قناة تيليجرام: {'✅ نجح' if ok else '⚠️ فشل'}")
        problems += 0 if ok else 1

    return 0 if problems == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="حارس بوت نور الإسلام")
    parser.add_argument("--once", action="store_true", help="دورة واحدة ثم خروج")
    parser.add_argument("--status", action="store_true", help="تقرير الحالة الآن")
    parser.add_argument("--force-fix", action="store_true", help="إصلاح فوري متجاهلاً الميزانية")
    parser.add_argument("--selftest", action="store_true", help="فحص ذاتي للحارس وقنواته")
    args = parser.parse_args()

    if args.selftest:
        return selftest(config)

    if args.status:
        results = checks.run_all(config)
        print(format_status(results, load_state(config), config))
        for incident in recent_incidents(config, 10):
            when = time.strftime("%m-%d %H:%M", time.localtime(incident.get("ts", 0)))
            print(f"  {when} · {incident.get('name', '')} → {incident.get('action', incident.get('outcome', ''))}")
        return 0

    if args.once or args.force_fix:
        outcome = run_cycle(config, force_fix=args.force_fix)
        print(format_status(outcome["results"], load_state(config), config))
        return 0 if outcome["summary"]["healthy"] else 1

    # ── التشغيل المستمرّ ───────────────────────────────────────────
    print(f"🛡️ الحارس بدأ — كل {config.interval} ثانية على {config.target_dir}")
    fixers.log(config, f"بدء الحارس (كل {config.interval} ثانية)")
    while True:
        try:
            run_cycle(config, verbose=False)
        except KeyboardInterrupt:
            print("\nتوقّف الحارس.")
            return 0
        except Exception:  # noqa: BLE001 - الحارس لا يسقط مهما حدث
            fixers.log(config, "خطأ غير متوقّع في دورة الحارس (تابع)")
        time.sleep(config.interval)


if __name__ == "__main__":
    sys.exit(main())
