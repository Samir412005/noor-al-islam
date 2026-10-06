"""حالة الحارس: الملف الحيوي، الحوادث، وميزانيات التنبيه والإصلاح."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import GuardianConfig


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_heartbeat(cfg: GuardianConfig) -> None:
    cfg.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.heartbeat_file.write_text(str(int(time.time())), encoding="utf-8")


def heartbeat_age(cfg: GuardianConfig) -> float | None:
    try:
        return time.time() - int(cfg.heartbeat_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def load_state(cfg: GuardianConfig) -> dict:
    try:
        return json.loads(cfg.status_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"alerts": {}, "fixes": {}, "last_status": {}, "incidents": 0}


def save_state(cfg: GuardianConfig, state: dict) -> None:
    _atomic_write(cfg.status_file, json.dumps(state, ensure_ascii=False, indent=2))


def record_incident(cfg: GuardianConfig, payload: dict) -> None:
    cfg.incidents_file.parent.mkdir(parents=True, exist_ok=True)
    with cfg.incidents_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": int(time.time()), **payload}, ensure_ascii=False) + "\n")


def recent_incidents(cfg: GuardianConfig, limit: int = 20) -> list[dict]:
    if not cfg.incidents_file.exists():
        return []
    lines = cfg.incidents_file.read_text(encoding="utf-8").splitlines()[-limit:]
    out: list[dict] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _prune(bucket: dict, window: int) -> None:
    cutoff = time.time() - window
    for key in list(bucket):
        bucket[key] = [ts for ts in bucket[key] if ts > cutoff]
        if not bucket[key]:
            bucket.pop(key, None)


def can_alert(state: dict, key: str, cooldown: int) -> bool:
    _prune(state.setdefault("alerts", {}), max(cooldown * 4, 3600))
    last = state["alerts"].get(key) or []
    return not last or (time.time() - max(last)) >= cooldown


def mark_alert(state: dict, key: str) -> None:
    state.setdefault("alerts", {}).setdefault(key, []).append(time.time())


def can_fix(state: dict, key: str, budget_per_hour: int) -> bool:
    _prune(state.setdefault("fixes", {}), 3600)
    return len(state["fixes"].get(key) or []) < budget_per_hour


def mark_fix(state: dict, key: str) -> None:
    """يُسجّل إصلاحاً في الميزانية (منعاً لحلقات الإصلاح)."""
    state.setdefault("fixes", {}).setdefault(key, []).append(time.time())
    state["incidents"] = int(state.get("incidents") or 0) + 1


def format_status(results: list, state: dict, cfg: GuardianConfig) -> str:
    """تقرير نصّي عربي يُرسل عبر قنوات التنبيه."""
    lines: list[str] = []
    healthy = all(r.ok for r in results)
    lines.append(("✅ البوت سليم" if healthy else "⚠️ يوجد خلل"))
    for result in results:
        mark = "✅" if result.ok else ("🛑" if result.severity == "critical" else "⚠️")
        lines.append(f"{mark} {result.name}: {result.detail}")
    age = heartbeat_age(cfg)
    if age is not None:
        lines.append(f"⏱️ آخر نبضة للحارس: قبل {int(age)} ثانية")
    incidents = recent_incidents(cfg, 5)
    if incidents:
        lines.append("— آخر الحوادث —")
        for item in incidents[-5:]:
            when = time.strftime("%H:%M", time.localtime(item.get("ts", 0)))
            lines.append(f"• {when} {item.get('name', '')}: {item.get('action', '')}")
    return "\n".join(lines)
