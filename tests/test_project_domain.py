from __future__ import annotations

import importlib
from pathlib import Path


def test_caelus_domain_and_schema_are_owned_by_project():
    domain = importlib.import_module("projects.caelus.domain")
    project_schemas = importlib.import_module("projects.caelus.schemas")

    assert domain.SIGN_ORDER
    assert domain.SIGN_NAMES["ru"]
    assert domain.CONTENT_FIELDS
    assert domain.DIVERSITY_META_FIELDS
    assert domain.FIELD_LIMITS
    assert callable(domain.display_date)
    assert callable(project_schemas.field_schema)
    assert callable(project_schemas.diversity_meta_schema)
    assert callable(project_schemas.sign_schema)
    assert callable(project_schemas.output_schema)
    assert callable(project_schemas.single_sign_output_schema)
    assert callable(project_schemas.single_field_output_schema)


def test_legacy_content_engine_schema_module_is_removed():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "content_engine").exists()


def test_card_and_publication_order_reuse_project_domain():
    domain = importlib.import_module("projects.caelus.domain")
    generate_cards = importlib.import_module("generate_cards")
    publish_telegram = importlib.import_module("publish_telegram")

    assert generate_cards.SIGN_ORDER is domain.SIGN_ORDER
    assert generate_cards.SIGN_NAMES_RU is domain.SIGN_NAMES["ru"]
    assert publish_telegram.SIGN_ORDER is domain.SIGN_ORDER
