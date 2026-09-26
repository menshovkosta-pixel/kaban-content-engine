from __future__ import annotations

from typing import Any

from .domain import CONTENT_FIELDS, DIVERSITY_META_FIELDS, FIELD_LIMITS, SIGN_ORDER


def field_schema(field: str) -> dict[str, Any]:
    if field not in FIELD_LIMITS:
        raise ValueError(f"Неизвестное поле контента: {field}")
    minimum, maximum = FIELD_LIMITS[field]
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def diversity_meta_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "theme": {"type": "string", "minLength": 3, "maxLength": 80},
            "situation": {"type": "string", "minLength": 3, "maxLength": 120},
            "tone": {"type": "string", "minLength": 3, "maxLength": 60},
            "advice_pattern": {"type": "string", "minLength": 3, "maxLength": 100},
        },
        "required": list(DIVERSITY_META_FIELDS),
    }


def sign_schema() -> dict[str, Any]:
    properties = {field: field_schema(field) for field in CONTENT_FIELDS}
    properties["_diversity"] = diversity_meta_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": [*CONTENT_FIELDS, "_diversity"],
    }


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "signs": {
                "type": "object",
                "additionalProperties": False,
                "properties": {key: sign_schema() for key in SIGN_ORDER},
                "required": SIGN_ORDER,
            }
        },
        "required": ["signs"],
    }


def single_sign_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"forecast": sign_schema()},
        "required": ["forecast"],
    }


def single_field_output_schema(field: str) -> dict[str, Any]:
    """JSON schema для точечной перегенерации одного смыслового блока."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"text": field_schema(field)},
        "required": ["text"],
    }
