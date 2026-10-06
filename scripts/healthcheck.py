#!/usr/bin/env python3
"""فحص جاهزية بوت «نور الإسلام» (NoorIslamBot) داخل بيئة النشر.

يفحص ثلاث مجموعات:

1. **البيانات المُجمَّعة**: وجود وصلاحية ``app/data/quran_uthmani.json`` (١١٤ سورة)،
   و``app/data/adhkar.json`` (أبواب وعناصر)، و``app/data/occasions.json``.
2. **الذاكرة (RSS) والاستيراد**: قابلية استيراد وحدات التطبيق (الموجّه، الحواجز،
   الوكلاء، الخدمات)، وتحميل بيانات القرآن والأذكار في الذاكرة، مع قراءة حجم الذاكرة
   المقيمة للعملية (RSS) والذاكرة المتاحة على النظام، إضافةً إلى التحقّق من قابلية
   الكتابة في مجلّد قاعدة البيانات (يُتجاهلها لو كان ``DB_PATH=:memory:``).
3. **الاتصال بتيليجرام**: نداء ``getMe`` إن كان ``BOT_TOKEN`` مضبوطاً (يُتخطّى مع
   ``--offline``). فشل النداء = فشل الفحص.

الاستعمال::

    python scripts/healthcheck.py              # كل الفحوص
    python scripts/healthcheck.py --offline    # بلا شبكة (مناسب لـHEALTHCHECK في Docker)
    python scripts/healthcheck.py --json       # مخرجات JSON للآلات

رموز الخروج: ``0`` نجاح، ``1`` فشل فحص، ``2`` خطأ في الاستعمال/البيئة (مثل غياب
مكتبة لازمة). السكربت لا يطبع ``BOT_TOKEN`` أبداً.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "app" / "data"
QURAN_FILE = DATA_DIR / "quran_uthmani.json"
ADHKAR_FILE = DATA_DIR / "adhkar.json"
OCCASIONS_FILE = DATA_DIR / "occasions.json"

API_BASE = "https://api.telegram.org"
LOW_MEMORY_MB = 64.0        # أقل من هذا = فشل
TIGHT_MEMORY_MB = 256.0     # أقل من هذا = تحذير

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


# ── أدوات مساعدة ──────────────────────────────────────────────────
def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        return
    except Exception:
        pass
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def read_rss_bytes() -> int | None:
    """حجم الذاكرة المقيمة للعملية (Resident Set Size) من /proc/self/status."""
    path = Path("/proc/self/status")
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def read_available_memory_bytes() -> int | None:
    """الذاكرة المتاحة على النظام من /proc/meminfo (Linux فقط)."""
    path = Path("/proc/meminfo")
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def mb(value: int | None) -> str:
    return "غير متوفر" if value is None else f"{value / (1024 * 1024):.1f} م.ب"


def rel(path: Path) -> str:
    """مسار مختصر بالنسبة لجذر المشروع، مع التحمّل للمسارات الخارجة عنه."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def json_loadable(path: Path) -> bool:
    """يتحقّق من أنّ الملف JSON صالح قبل تحميله في الذاكرة (تفادي جلب من الشبكة)."""
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


class Report:
    """يجمع نتائج الفحوص ويطبعها."""

    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
        self.checks.append({"name": name, "ok": bool(ok), "warn": bool(warn), "detail": detail})

    @property
    def failures(self) -> list[dict]:
        return [c for c in self.checks if not c["ok"] and not c["warn"]]

    def print_human(self) -> None:
        for check in self.checks:
            if check["warn"] and check["ok"]:
                icon = "⚠️"
            elif check["ok"]:
                icon = "✓"
            else:
                icon = "✗"
            line = f"{icon} {check['name']}"
            if check["detail"]:
                line += f" — {check['detail']}"
            print(line)


# ── الفحص ١: ملفات البيانات المُجمَّعة ────────────────────────────
def check_data_files(report: Report) -> None:
    ayah_count = 0

    if not QURAN_FILE.exists():
        report.add("ملف القرآن المُجمَّع", False, f"غير موجود: {rel(QURAN_FILE)} — شغّل scripts/fetch_quran.py")
    else:
        try:
            payload = json.loads(QURAN_FILE.read_text(encoding="utf-8"))
            surahs = payload.get("surahs") or []
            ayah_count = sum(len(s.get("ayahs") or []) for s in surahs)
            ok = len(surahs) == 114 and ayah_count > 6000
            report.add(
                "ملف القرآن المُجمَّع",
                ok,
                f"{len(surahs)} سورة / {ayah_count} آية ({QURAN_FILE.stat().st_size / (1024 * 1024):.1f} م.ب)",
            )
        except (OSError, ValueError) as exc:
            report.add("ملف القرآن المُجمَّع", False, f"تعذّر قراءته: {type(exc).__name__}")

    if not ADHKAR_FILE.exists():
        report.add("ملف الأذكار المُجمَّع", False, f"غير موجود: {rel(ADHKAR_FILE)} — شغّل scripts/fetch_adhkar.py")
    else:
        try:
            payload = json.loads(ADHKAR_FILE.read_text(encoding="utf-8"))
            categories = payload.get("categories") or []
            items = sum(len(c.get("items") or []) for c in categories)
            report.add(
                "ملف الأذكار المُجمَّع",
                bool(categories) and items > 0,
                f"{len(categories)} باباً / {items} عنصراً",
            )
        except (OSError, ValueError) as exc:
            report.add("ملف الأذكار المُجمَّع", False, f"تعذّر قراءته: {type(exc).__name__}")

    if not OCCASIONS_FILE.exists():
        report.add("ملف المناسبات", False, f"غير موجود: {rel(OCCASIONS_FILE)}")
    else:
        try:
            data = json.loads(OCCASIONS_FILE.read_text(encoding="utf-8"))
            ok = isinstance(data, list) and any(isinstance(item, dict) for item in data)
            report.add("ملف المناسبات", ok, f"{len(data) if isinstance(data, list) else '?'} مناسبة")
        except (OSError, ValueError) as exc:
            report.add("ملف المناسبات", False, f"تعذّر قراءته: {type(exc).__name__}")


# ── الفحص ٢: الاستيراد والذاكرة ───────────────────────────────────
def check_memory_and_imports(report: Report) -> None:
    rss_before = read_rss_bytes()
    modules = [
        ("الإعدادات", "app.config"),
        ("النصوص", "app.text"),
        ("النماذج", "app.models"),
        ("الحواجز", "app.guardrails"),
        ("قاعدة البيانات", "app.db"),
        ("محرّك الذكاء الاصطناعي", "app.llm"),
        ("الموجّه", "app.router"),
        ("الوكلاء", "app.agents"),
        ("خدمة القرآن", "app.services.quran_api"),
        ("خدمة الحديث", "app.services.hadith_api"),
        ("خدمة المواقيت", "app.services.prayer_api"),
    ]
    failed: list[str] = []
    for label, module in modules:
        try:
            __import__(module)
        except Exception as exc:  # pragma: no cover - يعتمد على البيئة
            failed.append(f"{label} ({module}): {type(exc).__name__}")
    report.add(
        "استيراد وحدات التطبيق",
        not failed,
        "كل الوحدات جاهزة" if not failed else "فشل: " + "؛ ".join(failed),
    )

    # تحميل البيانات في الذاكرة: أصدق اختبار لصلاحية الذاكرة قبل التشغيل.
    loaded: list[str] = []
    load_errors: list[str] = []
    if QURAN_FILE.exists() and json_loadable(QURAN_FILE):
        try:
            from app.services import quran_api

            surahs = len(quran_api.load_quran()["surahs"])
            loaded.append(f"القرآن ({surahs} سورة)")
        except Exception as exc:
            load_errors.append(f"القرآن: {type(exc).__name__}")
    if ADHKAR_FILE.exists() and json_loadable(ADHKAR_FILE):
        try:
            from app.agents import adhkar as adhkar_agent

            categories = len(adhkar_agent.load_data()["categories"])
            loaded.append(f"الأذكار ({categories} باباً)")
        except Exception as exc:
            load_errors.append(f"الأذكار: {type(exc).__name__}")
    report.add(
        "تحميل البيانات في الذاكرة",
        not load_errors,
        "؛ ".join(loaded + load_errors) or "لا ملفات صالحة لتحميلها — راجع فحص البيانات أعلاه",
    )

    rss_after = read_rss_bytes()
    available = read_available_memory_bytes()
    if rss_after is None and available is None:
        report.add("صلاحية الذاكرة (RSS)", True, "لا تتوفّر /proc على هذا النظام — تُخطّى القراءة", warn=True)
    else:
        report.add(
            "صلاحية الذاكرة (RSS)",
            available is None or available >= LOW_MEMORY_MB * 1024 * 1024,
            f"ذاكرة العملية: {mb(rss_after)} (قبل التحميل: {mb(rss_before)}) • المتاح للنظام: {mb(available)}",
        )
        if available is not None and LOW_MEMORY_MB * 1024 * 1024 <= available < TIGHT_MEMORY_MB * 1024 * 1024:
            report.add("هامش الذاكرة", True, f"الذاكرة المتاحة ضيّقة: {mb(available)}", warn=True)

    # قابلية الكتابة في مجلّد قاعدة البيانات (يُتخطّى مع :memory:)
    db_path = (os.environ.get("DB_PATH") or "").strip() or str(ROOT / "data" / "noor.db")
    if db_path == ":memory:":
        report.add("مجلّد قاعدة البيانات", True, "DB_PATH=:memory: — لا حاجة لقرص")
    else:
        parent = Path(db_path).expanduser()
        parent = parent if parent.is_absolute() else (ROOT / parent)
        target = parent.parent
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".healthcheck-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            report.add("مجلّد قاعدة البيانات", True, f"قابل للكتابة: {target}")
        except OSError as exc:
            report.add("مجلّد قاعدة البيانات", False, f"غير قابل للكتابة {target}: {exc.strerror or exc}")


# ── الفحص ٣: getMe ────────────────────────────────────────────────
async def _get_me(token: str, timeout: float) -> dict:
    url = f"{API_BASE}/bot{token}/getMe"
    if httpx is not None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"فشل الاتصال بتيليجرام: {type(exc).__name__}") from exc
            body = response.text
    else:  # بديل قياسي

        def _blocking() -> str:
            request = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as handle:  # noqa: S310
                    return handle.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                return exc.read().decode("utf-8", "replace")
            except urllib.error.URLError as exc:
                raise RuntimeError(f"فشل الاتصال بتيليجرام: {type(exc).__name__}") from exc

        body = await asyncio.to_thread(_blocking)

    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("استجابة غير صالحة من تيليجرام") from exc
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("description") or "ردّ غير ناجح"))
    return payload.get("result") or {}


def check_telegram(report: Report, *, offline: bool, timeout: float) -> None:
    token = (os.environ.get("BOT_TOKEN") or "").strip()
    if offline:
        report.add("نداء getMe", True, "متخطّى (--offline)", warn=True)
        return
    if not token:
        report.add("نداء getMe", True, "متخطّى (BOT_TOKEN غير مضبوط)", warn=True)
        return
    try:
        me = asyncio.run(_get_me(token, timeout))
    except RuntimeError as exc:
        report.add("نداء getMe", False, str(exc))
        return
    except Exception as exc:  # pragma: no cover
        report.add("نداء getMe", False, f"خطأ غير متوقّع: {type(exc).__name__}")
        return
    username = me.get("username") or "(بلا معرّف)"
    report.add("نداء getMe", True, f"البوت: {me.get('first_name', '')} @{username}")


# ── نقطة الدخول ───────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="فحص جاهزية بوت نور الإسلام (لا يطبع التوكن).")
    parser.add_argument("--offline", action="store_true", help="تخطّي نداء getMe (بلا شبكة)")
    parser.add_argument("--json", action="store_true", help="إخراج النتائج بصيغة JSON")
    parser.add_argument("--timeout", type=float, default=15.0, help="مهلة نداء تيليجرام بالثواني")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env()

    report = Report()
    check_data_files(report)
    check_memory_and_imports(report)
    check_telegram(report, offline=args.offline, timeout=args.timeout)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not report.failures,
                    "checks": report.checks,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("── فحص جاهزية نور الإسلام ──")
        report.print_human()
        if report.failures:
            print(f"\n✗ فشل {len(report.failures)} فحصاً.")
        else:
            print("\n✓ كل الفحوص ناجحة.")

    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
