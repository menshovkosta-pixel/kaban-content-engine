import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import admin_app


def test_admin_bind_defaults(monkeypatch):
    monkeypatch.delenv("KABAN_ADMIN_HOST", raising=False)
    monkeypatch.delenv("KABAN_ADMIN_PORT", raising=False)
    assert admin_app.admin_host() == "127.0.0.1"
    assert admin_app.admin_port() == 8088


def test_admin_bind_environment_overrides(monkeypatch):
    monkeypatch.setenv("KABAN_ADMIN_HOST", "0.0.0.0")
    monkeypatch.setenv("KABAN_ADMIN_PORT", "18088")
    assert admin_app.admin_host() == "0.0.0.0"
    assert admin_app.admin_port() == 18088


@pytest.mark.parametrize("raw", ["0", "65536", "abc", ""])
def test_admin_port_rejects_invalid_values(monkeypatch, raw):
    monkeypatch.setenv("KABAN_ADMIN_PORT", raw)
    with pytest.raises(ValueError, match="KABAN_ADMIN_PORT"):
        admin_app.admin_port()


def test_healthz_is_minimal_json_and_does_not_redirect():
    server = ThreadingHTTPServer(("127.0.0.1", 0), admin_app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/healthz", timeout=3
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert response.headers.get_content_type() == "application/json"
            assert payload == {"status": "ok", "service": "kaban-admin"}
            rendered = json.dumps(payload)
            assert "OPENAI" not in rendered
            assert "TELEGRAM" not in rendered
            assert "generated" not in rendered.lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
