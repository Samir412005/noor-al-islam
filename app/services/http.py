"""عميل HTTP غير متزامن مع تخزين مؤقت (TTL) وإعادة محاولة.

كل الوصول للإنترنت في المشروع يمرّ من هنا، ما يجعل الوكلاء:
- أسرع (الذاكرة المؤقتة),
- أخفّ على المصادر الخارجية,
- وقابلين للاختبار بحقن عميل وهمي.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

UA = "NoorIslamBot/2.0 (+https://t.me/NoorIslamSamir2026Bot)"
DEFAULT_TTL = 900  # 15 دقيقة


class _CacheEntry:
    __slots__ = ("value", "expires")

    def __init__(self, value: Any, expires: float) -> None:
        self.value = value
        self.expires = expires


class CachedHTTP:
    def __init__(self, *, timeout: float = 15.0, retries: int = 3, user_agent: str = UA) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": user_agent, "Accept": "application/json, text/plain, */*"},
            follow_redirects=True,
        )
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()
        self.retries = retries
        self.hits = 0
        self.misses = 0

    # ── داخلي ──────────────────────────────────────────────────────
    def _key(self, url: str, params: dict | None) -> str:
        if not params:
            return url
        ordered = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{url}?{ordered}"

    def _cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry and entry.expires > time.monotonic():
            self.hits += 1
            return entry.value
        if entry:
            self._cache.pop(key, None)
        self.misses += 1
        return None

    async def _get_with_retry(self, url: str, params: dict | None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = await self._client.get(url, params=params)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:  # pragma: no cover
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                await asyncio.sleep(0.4 * (2**attempt))
        raise httpx.TransportError(f"فشل الاتصال بـ {url}") from last_error

    # ── واجهة عامة ─────────────────────────────────────────────────
    async def get_json(
        self,
        url: str,
        params: dict | None = None,
        *,
        ttl: int | None = DEFAULT_TTL,
    ) -> Any:
        key = self._key(url, params)
        if ttl:
            cached = self._cached(key)
            if cached is not None:
                return cached
        response = await self._get_with_retry(url, params)
        response.raise_for_status()
        data = response.json()
        if ttl:
            async with self._lock:
                self._cache[key] = _CacheEntry(data, time.monotonic() + ttl)
        return data

    async def get_bytes(self, url: str, *, ttl: int | None = 3600) -> bytes:
        """يجلب ملفاً ثنائياً (صوت/صورة) — بلا تخزين افتراضي طويل."""
        key = self._key(url, None)
        if ttl:
            cached = self._cached(key)
            if cached is not None:
                return cached
        response = await self._get_with_retry(url, None)
        response.raise_for_status()
        content = response.content
        if ttl:
            async with self._lock:
                self._cache[key] = _CacheEntry(content, time.monotonic() + ttl)
        return content

    def clear_cache(self) -> None:
        self._cache.clear()

    async def aclose(self) -> None:
        await self._client.aclose()


# عميل مشترك للعملية بأكملها (يُستبدل في الاختبارات بحقن بديل).
http = CachedHTTP()
