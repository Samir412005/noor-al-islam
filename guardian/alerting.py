"""قنوات التنبيه — متعدّدة ومرتّبة بحسب خطورة الحالة.

الفكرة الجوهرية: **إن سقط البوت نفسه، لا تصل التنبيهات عبره.** لذلك:
1. تيليجرام (عبر البوت الهدف) — الأسرع، ويُستعمل عندما يكون البوت سليماً.
2. تقويم Google — تنبيه على هاتف المالك عندما يكون البوت **متوقّفاً** (قناة مستقلّة تماماً).
3. مسودّة Gmail — سجلّ مكتوب يبقى في بريده.
4. بوت الحارس الخاص (إن أُعطي توكن) — أقوى قناة، لكنها تتطلّب خطوة يدوية من @BotFather.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

from .config import GuardianConfig

SEVERITY_LABEL = {"critical": "🛑 عطل", "warning": "⚠️ تحذير", "info": "ℹ️ معلومة", "ok": "✅ تعافٍ"}


def _telegram_send(cfg: GuardianConfig, chat_id: int, text: str) -> bool:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return bool(json.loads(response.read().decode() or "{}").get("ok"))
    except Exception:  # noqa: BLE001
        return False


def notify_telegram(cfg: GuardianConfig, title: str, body: str, severity: str) -> bool:
    """يرسل عبر البوت الهدف (المسار الأسرع — يعمل عندما يكون البوت سليماً)."""
    if not (cfg.notify_telegram and cfg.bot_token):
        return False
    text = f"<b>{SEVERITY_LABEL.get(severity, '')} {title}</b>\n\n{body}"
    sent = False
    for chat_id in cfg.admin_chat_ids:
        sent = _telegram_send(cfg, chat_id, text) or sent
    return sent


def notify_guardian_bot(cfg: GuardianConfig, title: str, body: str, severity: str) -> bool:
    """يرسل عبر بوت الحارس المستقل — القناة الأقوى إن وُجد توكن لها."""
    if not cfg.guardian_bot_token:
        return False
    text = f"<b>{SEVERITY_LABEL.get(severity, '')} {title}</b>\n\n{body}"
    payload = {"chat_id": None, "text": text, "parse_mode": "HTML"}
    ok = False
    for chat_id in cfg.admin_chat_ids:
        payload["chat_id"] = chat_id
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{cfg.guardian_bot_token}/sendMessage",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                ok = bool(json.loads(response.read().decode() or "{}").get("ok")) or ok
        except Exception:  # noqa: BLE001
            continue
    return ok


def notify_calendar(cfg: GuardianConfig, title: str, body: str, severity: str) -> bool:
    """ينشئ حدثاً في تقويم المالك — قناة تنبيه مستقلّة عن تيليجرام بالكامل."""
    if not cfg.notify_calendar:
        return False
    now = int(time.time())
    payload = {
        "calendarId": cfg.calendar_id,
        "summary": f"{SEVERITY_LABEL.get(severity, '')} نور الإسلام: {title}",
        "description": f"{body}\n\n— أرسله حارس البوت (Noor Guardian) في {time.strftime('%Y-%m-%d %H:%M')}",
        "start": {"dateTime": _iso(now)},
        "end": {"dateTime": _iso(now + 900)},
    }
    return _connector(cfg, "google_calendar.events.create", payload)


def notify_draft(cfg: GuardianConfig, title: str, body: str, severity: str) -> bool:
    """يحفظ مسودّة في Gmail — سجلّ يبقى حتى لو مات كل شيء."""
    if not cfg.notify_draft:
        return False
    payload = {
        "to": cfg.owner_email,
        "subject": f"{SEVERITY_LABEL.get(severity, '')} نور الإسلام — {title}",
        "body": body,
    }
    return _connector(cfg, "google_gmail.drafts.create", payload)


def _iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch)) + time.strftime("%z", time.localtime(epoch))[:3] + ":" + time.strftime("%z", time.localtime(epoch))[3:]


def _connector(cfg: GuardianConfig, action: str, payload: dict) -> bool:
    """ينفّذ إجراء الموصّل عبر connector-cli (متاح في بيئة Zentor)."""
    try:
        done = subprocess.run(
            ["connector-cli", "provider", "invoke", action, "--input", json.dumps(payload, ensure_ascii=False), "--confirm"],
            capture_output=True, text=True, timeout=60,
        )
        if done.returncode != 0:
            return False
        return bool(json.loads(done.stdout or "{}").get("ok"))
    except Exception:  # noqa: BLE001
        return False


def dispatch(cfg: GuardianConfig, title: str, body: str, severity: str) -> list[str]:
    """يجرّب القنوات بحسب الخطورة ويعيد أسماء ما نجح منها.

    في العطل الحرج (البوت متوقّف) لا نعتمد على البوت — التقويم أولاً.
    """
    channels: list[str] = []
    if severity == "critical":
        order = (("guardian_bot", notify_guardian_bot), ("calendar", notify_calendar),
                 ("telegram", notify_telegram), ("draft", notify_draft))
    else:
        order = (("guardian_bot", notify_guardian_bot), ("telegram", notify_telegram),
                 ("calendar", notify_calendar), ("draft", notify_draft))
    for name, function in order:
        try:
            if function(cfg, title, body, severity):
                channels.append(name)
        except Exception:  # noqa: BLE001 - قناة فاشلة لا تُسقط البقية
            continue
    return channels
