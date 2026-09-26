from uuid import uuid4
import pytest
from kaban.cloud.models import ExecutionCommand


def test_execution_command_requires_project_and_idempotency():
    with pytest.raises(ValueError):
        ExecutionCommand(uuid4(), "", "generate", None, None, None, {}, "scheduler", "x")
    with pytest.raises(ValueError):
        ExecutionCommand(uuid4(), "caelus", "generate", None, None, None, {}, "scheduler", "")


def test_execution_payload_is_immutable():
    command = ExecutionCommand(uuid4(), "caelus", "generate", None, None, None, {"x": 1}, "scheduler", "x")
    with pytest.raises(TypeError):
        command.payload["x"] = 2
