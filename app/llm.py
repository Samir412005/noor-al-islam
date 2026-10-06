"""عميل LLM متوافق مع واجهة OpenAI (OpenRouter / Groq / OpenAI / Gemini-compat / Ollama).

مبادئ التصميم:
- **اختياري تماماً**: البوت يعمل كاملاً بدون مفتاح.
- **أدوات (tools)**: وكيل العلم لا يخترع نصوصاً، بل يستدعي أدوات القرآن والحديث
  ويقتبس منها؛ هذا ما يمنع الهلوسة في بابٍ لا تسامح فيه.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from .config import settings

log = logging.getLogger(__name__)

ToolImpl = Callable[..., Awaitable[str]]
MAX_TOOL_ROUNDS = 3


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model if model is not None else settings.llm_model
        self.max_tokens = max_tokens or settings.llm_max_tokens
        self.temperature = (
            temperature if temperature is not None else settings.llm_temperature
        )
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(90.0))

    @property
    def enabled(self) -> bool:
        if not (self.base_url and self.model):
            return False
        if self.api_key:
            return True
        return any(h in self.base_url for h in ("127.0.0.1", "localhost", "0.0.0.0", "host.docker.internal"))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── نداء أساسي ─────────────────────────────────────────────────
    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter" in self.base_url:
            headers["HTTP-Referer"] = "https://t.me/NoorIslamSamir2026Bot"
            headers["X-Title"] = "Noor Islam Bot"
        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise LLMError(f"HTTP {exc.response.status_code}: {detail}") from exc
        except (httpx.TransportError, ValueError) as exc:
            raise LLMError(f"تعذّر الوصول إلى نموذج الذكاء الاصطناعي: {exc}") from exc

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_impls: dict[str, ToolImpl] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
    ) -> str:
        """محادثة مع دعم استدعاء الأدوات (tool calling)."""
        if not self.enabled:
            raise LLMError("LLM غير مفعّل")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        conversation = list(messages)
        for _ in range(MAX_TOOL_ROUNDS + 1):
            data = await self._post(payload)
            choices = data.get("choices") or []
            if not choices:
                raise LLMError("استجابة فارغة من النموذج")
            message = choices[0].get("message", {})
            calls = message.get("tool_calls") or []

            if not calls:
                return (message.get("content") or "").strip()

            # تنفيذ الأدوات وإعادة إطعامها للنموذج
            conversation.append(message)
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                impl = (tool_impls or {}).get(name)
                try:
                    result = await impl(**args) if impl else f"أداة غير معروفة: {name}"
                except TypeError:
                    result = await impl(args) if impl else f"أداة غير معروفة: {name}"
                except Exception as exc:  # pragma: no cover
                    log.warning("tool %s failed: %s", name, exc)
                    result = f"تعذّر تنفيذ الأداة: {type(exc).__name__}"
                log.info("tool_call %s -> %d chars", name, len(str(result)))
                if on_tool_result is not None:
                    try:
                        on_tool_result(name, str(result))
                    except Exception:  # pragma: no cover - المجمّع لا يُسقط الحوار
                        log.warning("on_tool_result failed for %s", name)
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", name),
                        "content": str(result)[:6000],
                    }
                )
            payload = {**payload, "messages": conversation}

        raise LLMError("تجاوز النموذج عدد جولات الأدوات المسموح")


llm = LLM()
