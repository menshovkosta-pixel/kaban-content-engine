from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


def test_valid_callable_loads():
    from kaban.scheduler.adapter import load_adapter
    mod=ModuleType("demo_adapter")
    def run(job,ctx): return None
    mod.run=run
    with patch.dict(sys.modules,{"demo_adapter":mod}):
        assert load_adapter("demo_adapter:run") is run


@pytest.mark.parametrize("entry", ["bad", ":run", "demo:"])
def test_bad_entrypoint_rejected(entry):
    from kaban.scheduler.adapter import AdapterLoadError, load_adapter
    with pytest.raises(AdapterLoadError): load_adapter(entry)


def test_missing_module_and_attribute_and_noncallable_rejected():
    from kaban.scheduler.adapter import AdapterLoadError, load_adapter
    with pytest.raises(AdapterLoadError): load_adapter("definitely_missing_xyz:run")
    mod=ModuleType("demo_missing")
    mod.value=42
    with patch.dict(sys.modules,{"demo_missing":mod}):
        with pytest.raises(AdapterLoadError): load_adapter("demo_missing:nope")
        with pytest.raises(AdapterLoadError): load_adapter("demo_missing:value")


def test_kaban_scheduler_has_no_direct_caelus_import():
    root=Path(__file__).resolve().parent.parent/"kaban"/"scheduler"
    source="\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    assert "projects.caelus" not in source
