from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_compose():
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_has_three_isolated_services_and_persistence():
    cfg = load_compose()
    services = cfg["services"]
    assert set(services) >= {"scheduler", "admin", "gateway"}
    scheduler = services["scheduler"]
    admin = services["admin"]
    gateway = services["gateway"]
    assert scheduler["command"] == ["python", "scheduler.py", "run", "--poll-seconds", "30"]
    assert admin["command"] == ["python", "admin_app.py"]
    assert scheduler["restart"] == "unless-stopped"
    assert admin["restart"] == "unless-stopped"
    assert gateway["restart"] == "unless-stopped"
    assert "ports" not in scheduler
    assert "ports" not in admin
    assert admin["expose"] == ["8088"]
    assert gateway["ports"] == ["80:80", "443:443"]
    expected = {"./data/generated:/app/generated", "./data/runtime:/app/runtime"}
    assert expected <= set(scheduler["volumes"])
    assert expected <= set(admin["volumes"])


def test_healthchecks_and_log_rotation_are_bounded():
    cfg = load_compose()
    scheduler = cfg["services"]["scheduler"]
    admin = cfg["services"]["admin"]
    assert scheduler["healthcheck"]["test"] == ["CMD", "python", "scheduler.py", "health", "--max-age-seconds", "120"]
    admin_cmd = " ".join(admin["healthcheck"]["test"])
    assert "127.0.0.1:8088/healthz" in admin_cmd
    for name in ("scheduler", "admin"):
        service = cfg["services"][name]
        assert "healthcheck" in service
        assert service["logging"]["driver"] == "json-file"
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "5"}


def test_application_environment_and_nonroot_user_are_explicit():
    cfg = load_compose()
    scheduler = cfg["services"]["scheduler"]
    admin = cfg["services"]["admin"]
    assert scheduler["environment"]["KABAN_RUNTIME_DIR"] == "/app/runtime"
    assert admin["environment"]["KABAN_RUNTIME_DIR"] == "/app/runtime"
    assert admin["environment"]["KABAN_ADMIN_HOST"] == "0.0.0.0"
    assert str(admin["environment"]["KABAN_ADMIN_PORT"]) == "8088"
    assert scheduler["user"] == "${KABAN_UID:-1000}:${KABAN_GID:-1000}"
    assert admin["user"] == "${KABAN_UID:-1000}:${KABAN_GID:-1000}"


def test_no_privileged_host_network_or_docker_socket():
    cfg = load_compose()
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    for service in cfg["services"].values():
        assert service.get("privileged") is not True
        assert service.get("network_mode") != "host"
    assert "/var/run/docker.sock" not in text


def test_caddy_requires_auth_and_only_proxies_admin():
    text = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "basic_auth" in text
    assert "{$KABAN_ADMIN_USER}" in text
    assert "{$KABAN_ADMIN_PASSWORD_HASH}" in text
    assert "reverse_proxy admin:8088" in text
    assert "scheduler" not in text


def test_gateway_receives_only_gateway_configuration_not_provider_secrets():
    gateway = load_compose()["services"]["gateway"]
    assert "env_file" not in gateway
    assert set(gateway["environment"]) == {
        "KABAN_PUBLIC_URL",
        "KABAN_ADMIN_USER",
        "KABAN_ADMIN_PASSWORD_HASH",
    }


def test_stage4_keeps_local_compose_cloud_independent():
    cfg = load_compose()
    for name in ("scheduler", "admin", "gateway"):
        environment = cfg["services"][name].get("environment", {})
        text = "\n".join(f"{k}={v}" for k, v in environment.items()) if isinstance(environment, dict) else "\n".join(environment)
        assert "SUPABASE" not in text
        assert "AWS_ACCESS_KEY_ID" not in text
        assert "AWS_SECRET_ACCESS_KEY" not in text
        assert "GITHUB_DISPATCH_TOKEN" not in text
