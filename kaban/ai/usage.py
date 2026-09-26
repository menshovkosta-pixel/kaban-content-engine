from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """Цена модели в USD за 1 млн токенов."""

    input_per_1m: float
    cached_input_per_1m: float
    output_per_1m: float
    long_context_threshold: int | None = None
    long_input_multiplier: float = 1.0
    long_output_multiplier: float = 1.0


# Тарифы фиксируются в release-коде, чтобы уже сохранённые generation.api_usage
# не меняли стоимость задним числом при будущих изменениях прайса.
# GPT-5.6 Luna: снимок тарифа для воспроизводимого расчёта стоимости (2026-09-23).
MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-5.6-luna": ModelPricing(
        input_per_1m=0.20,
        cached_input_per_1m=0.02,
        output_per_1m=1.20,
        long_context_threshold=272_000,
        long_input_multiplier=2.0,
        long_output_multiplier=1.5,
    ),
}


def pricing_for_model(model: str) -> ModelPricing | None:
    normalized = (model or "").strip().lower()
    if normalized in MODEL_PRICING:
        return MODEL_PRICING[normalized]
    # Поддерживаем date-suffixed алиасы одной и той же модели.
    for prefix, pricing in MODEL_PRICING.items():
        if normalized.startswith(prefix + "-"):
            return pricing
    return None


def _attr(obj: Any, name: str, default: Any = 0) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass
class UsageTracker:
    """Накапливает фактические usage-поля, возвращённые OpenAI API."""

    model: str
    request_count: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    priced_request_count: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    def add_openai_usage(self, usage: Any) -> None:
        if usage is None:
            return
        prompt = int(_attr(usage, "prompt_tokens", 0) or 0)
        completion = int(_attr(usage, "completion_tokens", 0) or 0)
        total = int(_attr(usage, "total_tokens", prompt + completion) or (prompt + completion))
        prompt_details = _attr(usage, "prompt_tokens_details", None)
        completion_details = _attr(usage, "completion_tokens_details", None)
        cached = int(_attr(prompt_details, "cached_tokens", 0) or 0)
        reasoning = int(_attr(completion_details, "reasoning_tokens", 0) or 0)
        cached = min(max(cached, 0), max(prompt, 0))
        reasoning = min(max(reasoning, 0), max(completion, 0))

        pricing = pricing_for_model(self.model)
        call_cost: float | None = None
        if pricing:
            input_multiplier = 1.0
            output_multiplier = 1.0
            if pricing.long_context_threshold and prompt > pricing.long_context_threshold:
                input_multiplier = pricing.long_input_multiplier
                output_multiplier = pricing.long_output_multiplier
            uncached = max(prompt - cached, 0)
            call_cost = (
                uncached * pricing.input_per_1m * input_multiplier
                + cached * pricing.cached_input_per_1m * input_multiplier
                + completion * pricing.output_per_1m * output_multiplier
            ) / 1_000_000
            self.estimated_cost_usd += call_cost
            self.priced_request_count += 1

        self.request_count += 1
        self.prompt_tokens += prompt
        self.cached_prompt_tokens += cached
        self.completion_tokens += completion
        self.reasoning_tokens += reasoning
        self.total_tokens += total
        self.calls.append({
            "prompt_tokens": prompt,
            "cached_prompt_tokens": cached,
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "total_tokens": total,
            "estimated_cost_usd": round(call_cost, 8) if call_cost is not None else None,
        })

    def to_dict(self, *, reasoning_effort: str | None = None) -> dict[str, Any]:
        pricing = pricing_for_model(self.model)
        return {
            "model": self.model,
            "reasoning_effort": reasoning_effort,
            "request_count": self.request_count,
            "prompt_tokens": self.prompt_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "uncached_prompt_tokens": max(self.prompt_tokens - self.cached_prompt_tokens, 0),
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8) if self.priced_request_count == self.request_count and self.request_count else (0.0 if self.request_count == 0 else None),
            "pricing": ({
                "currency": "USD",
                "input_per_1m": pricing.input_per_1m,
                "cached_input_per_1m": pricing.cached_input_per_1m,
                "output_per_1m": pricing.output_per_1m,
                "snapshot": "2026-09-23",
            } if pricing else None),
            "calls": list(self.calls),
        }


def provider_usage(provider: Any) -> dict[str, Any] | None:
    tracker = getattr(provider, "usage", None)
    if not isinstance(tracker, UsageTracker) or tracker.request_count == 0:
        return None
    return tracker.to_dict(reasoning_effort=getattr(provider, "reasoning_effort", None))


def merge_usage(existing: dict[str, Any] | None, delta: dict[str, Any] | None) -> dict[str, Any] | None:
    """Складывает usage при ручных Regenerate, сохраняя цену каждого вызова."""
    if not existing:
        return delta
    if not delta:
        return existing

    keys = (
        "request_count", "prompt_tokens", "cached_prompt_tokens", "uncached_prompt_tokens",
        "completion_tokens", "reasoning_tokens", "total_tokens",
    )
    result = dict(existing)
    for key in keys:
        result[key] = int(existing.get(key) or 0) + int(delta.get(key) or 0)

    old_cost = existing.get("estimated_cost_usd")
    new_cost = delta.get("estimated_cost_usd")
    result["estimated_cost_usd"] = (
        round(float(old_cost) + float(new_cost), 8)
        if old_cost is not None and new_cost is not None else None
    )
    result["model"] = delta.get("model") or existing.get("model")
    result["reasoning_effort"] = delta.get("reasoning_effort") or existing.get("reasoning_effort")
    result["pricing"] = delta.get("pricing") or existing.get("pricing")
    result["calls"] = list(existing.get("calls") or []) + list(delta.get("calls") or [])
    return result
