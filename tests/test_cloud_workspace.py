import os
from pathlib import Path
from uuid import uuid4
from kaban.cloud.workspace import execution_workspace, scoped_workspace_env


def test_workspace_is_isolated_and_cleaned(tmp_path):
    eid=uuid4()
    with execution_workspace(tmp_path,eid,"alpha") as paths:
        assert paths.project_root == tmp_path/str(eid)/"alpha"
        assert list(paths.generated.iterdir()) == []
        with scoped_workspace_env(paths):
            assert os.environ["KABAN_GENERATED_DIR"] == str(paths.generated)
            assert os.environ["KABAN_RUNTIME_DIR"] == str(paths.runtime)
            assert os.environ["KABAN_PERSISTENCE"] == "cloud"
    assert not (tmp_path/str(eid)).exists()


def test_workspace_cleanup_on_exception(tmp_path):
    eid=uuid4()
    try:
        with execution_workspace(tmp_path,eid,"alpha"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not (tmp_path/str(eid)).exists()
