from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import yaml

from kaban.cloud.config import CloudConfig

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / ".github" / "workflows" / "kaban-cloud-execution.yml"
CI = ROOT / ".github" / "workflows" / "kaban-cloud-ci.yml"


def _yaml(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_execution_workflow_is_dispatch_only_and_runs_one_canonical_execution():
    data = _yaml(EXECUTION)
    triggers = data["on"]
    assert set(triggers) == {"workflow_dispatch"}
    execution_input = triggers["workflow_dispatch"]["inputs"]["execution_id"]
    assert execution_input["required"] == "true"
    assert execution_input["type"] == "string"

    text = EXECUTION.read_text(encoding="utf-8")
    assert "schedule:" not in text
    assert "sleep 600" not in text
    assert "actions/upload-artifact" not in text
    assert text.count("cloud_runtime.py execute") == 1
    assert "KABAN_PERSISTENCE: cloud" in text
    assert "group: kaban-execution-${{ inputs.execution_id }}" in text
    assert "cancel-in-progress: false" in text
    for secret in (
        "KABAN_SUPABASE_URL",
        "KABAN_SUPABASE_SERVICE_KEY",
        "KABAN_R2_ENDPOINT_URL",
        "KABAN_R2_BUCKET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "OPENAI_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        assert secret in text


def test_ci_has_no_production_provider_credentials_or_calls():
    data = _yaml(CI)
    assert set(data["on"]) == {"push", "pull_request"}
    text = CI.read_text(encoding="utf-8")
    assert "pytest" in text
    assert "npm test" in text
    assert "typecheck" in text
    for secret in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "KABAN_SUPABASE_SERVICE_KEY", "AWS_SECRET_ACCESS_KEY"):
        assert secret not in text
    assert "cloud_runtime.py execute" not in text


def test_cloud_config_accepts_workflow_environment_names():
    env = {
        "KABAN_PERSISTENCE": "cloud",
        "KABAN_SUPABASE_URL": "https://db.example",
        "KABAN_SUPABASE_SERVICE_KEY": "service",
        "KABAN_R2_ENDPOINT_URL": "https://r2.example",
        "KABAN_R2_BUCKET": "bucket",
        "AWS_ACCESS_KEY_ID": "key",
        "AWS_SECRET_ACCESS_KEY": "secret",
    }
    with patch.dict(os.environ, env, clear=True):
        config = CloudConfig.from_env()
    assert config is not None
    assert config.r2_endpoint == "https://r2.example"
    assert config.r2_access_key_id == "key"
    assert config.r2_secret_access_key == "secret"

def test_cloud_cli_wires_one_execution_from_environment(tmp_path):
    from types import SimpleNamespace
    from uuid import UUID
    import kaban.cloud.cli as cloud_cli

    assert hasattr(cloud_cli, "execute_from_env"), "cloud CLI must expose provider wiring"
    execution_id = UUID("11111111-1111-4111-8111-111111111111")
    config = SimpleNamespace(
        supabase_url="https://db.example",
        supabase_service_key="service",
        r2_endpoint="https://r2.example",
        r2_bucket="bucket",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
    )
    fake_store = object()
    fake_artifacts = object()
    fake_registry = object()
    expected = SimpleNamespace(execution_id=execution_id, outcome="success", message="")
    with (
        patch.object(cloud_cli.CloudConfig, "from_env", return_value=config),
        patch.object(cloud_cli, "SupabaseControlStore", return_value=fake_store),
        patch.object(cloud_cli, "R2ArtifactStore", return_value=fake_artifacts),
        patch.object(cloud_cli, "project_registry", return_value=fake_registry),
        patch.object(cloud_cli, "run_execution", return_value=expected) as run,
    ):
        outcome = cloud_cli.execute_from_env(execution_id, owner="run:1", temp_root=tmp_path)
    assert outcome is expected
    run.assert_called_once()
    args, kwargs = run.call_args
    assert args == (execution_id,)
    assert kwargs["store"] is fake_store
    assert kwargs["artifacts"] is fake_artifacts
    assert kwargs["registry"] is fake_registry
    assert kwargs["temp_root"] == tmp_path
    assert kwargs["owner"] == "run:1"
    assert callable(kwargs["backup_hook"])
