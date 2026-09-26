from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .usage import UsageTracker


class AIProviderError(RuntimeError):
    """Ошибка AI transport, пригодная для отображения пользователю."""


@dataclass
class StructuredJsonClient:
    """Project-agnostic OpenAI client для strict JSON Schema responses."""

    model: str
    reasoning_effort: str = "low"
    usage: UsageTracker = field(init=False)

    def __post_init__(self) -> None:
        self.usage = UsageTracker(self.model)

    def request(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        if not os.getenv("OPENAI_API_KEY"):
            raise AIProviderError(
                "Не найден OPENAI_API_KEY. Создайте файл .env рядом с admin_app.py и добавьте строку "
                "OPENAI_API_KEY=<ВАШ_API_KEY>. Реальный ключ не отправляйте в чат и не добавляйте в ZIP."
            )

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIProviderError("Не установлен пакет openai. Выполните: python -m pip install -r requirements.txt") from exc

        try:
            response = OpenAI().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                reasoning_effort=self.reasoning_effort,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
            self.usage.add_openai_usage(response.usage)
            raw = response.choices[0].message.content
            if not raw:
                raise AIProviderError("AI provider вернул пустой ответ.")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise AIProviderError("AI provider вернул JSON неверного типа: ожидался object.")
            return payload
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError(f"Ошибка OpenAI provider: {exc}") from exc
