from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_is_nonroot_python310_image():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.10-slim-bookworm" in text
    assert "PYTHONUNBUFFERED=1" in text
    assert "USER kaban" in text
    assert "COPY requirements.txt" in text
    assert "pip install" in text
    assert "COPY --chown=kaban:kaban kaban" in text
    assert "COPY --chown=kaban:kaban projects" in text
    assert "COPY .env" not in text


def test_dockerignore_excludes_secrets_runtime_tests_and_caches():
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {
        ".env", ".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache",
        "runtime", "data", "generated", "tests", "docs", "*.pem", "*.key"
    }
    assert required <= patterns


def test_forbidden_live_paths_are_excluded_from_intended_build_context():
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for required in (".env", "runtime", "data", "__pycache__", ".pytest_cache", "*.pem", "*.key"):
        assert required in patterns


def test_stage4_cloud_credentials_are_not_baked_into_local_docker_image():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for secret in ("KABAN_SUPABASE_SERVICE_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_DISPATCH_TOKEN"):
        assert secret not in text
