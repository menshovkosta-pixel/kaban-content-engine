from __future__ import annotations

from pathlib import Path


def test_multipart_body_builds_project_agnostic_form(tmp_path: Path) -> None:
    from kaban.publishing.telegram import multipart_body

    image = tmp_path / "card.jpg"
    image.write_bytes(b"jpeg-bytes")

    body, content_type = multipart_body(
        {"chat_id": "-100123", "media": "[]"},
        [("file0", image)],
    )

    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in body
    assert b"-100123" in body
    assert b'name="file0"; filename="card.jpg"' in body
    assert b"jpeg-bytes" in body
    assert b"CAELUS" not in body


def test_telegram_client_sends_form_requests(monkeypatch) -> None:
    import json
    import urllib.parse
    import urllib.request

    from kaban.publishing.telegram import TelegramClient

    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"ok": True, "result": self.payload}).encode("utf-8")

    def fake_urlopen(req, timeout):
        calls.append((req, timeout))
        method = req.full_url.rsplit("/", 1)[-1]
        if method == "getChat":
            return Response({"id": -100123, "title": "Channel"})
        return Response({"message_id": 77})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = TelegramClient("secret-token", timeout=42, retries=0)
    sent = client.send_message("-100123", "<b>Hello</b>")
    chat = client.get_chat("-100123")

    assert sent == {"message_id": 77}
    assert chat == {"id": -100123, "title": "Channel"}
    assert [req.full_url for req, _ in calls] == [
        "https://api.telegram.org/botsecret-token/sendMessage",
        "https://api.telegram.org/botsecret-token/getChat",
    ]
    assert [timeout for _, timeout in calls] == [42, 42]

    send_payload = urllib.parse.parse_qs(calls[0][0].data.decode("utf-8"))
    assert send_payload == {
        "chat_id": ["-100123"],
        "text": ["<b>Hello</b>"],
        "parse_mode": ["HTML"],
        "disable_web_page_preview": ["true"],
    }


def test_telegram_client_retries_network_errors(monkeypatch) -> None:
    import json
    import urllib.error
    import urllib.request

    import kaban.publishing.telegram as telegram
    from kaban.publishing.telegram import TelegramClient

    attempts = 0
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"ok": True, "result": {"id": -100123}}).encode("utf-8")

    def fake_urlopen(req, timeout):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.URLError("temporary")
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(telegram.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = TelegramClient("token", retries=2).get_chat("-100123")

    assert result == {"id": -100123}
    assert attempts == 3
    assert sleeps == [2, 4]


def test_telegram_client_does_not_retry_http_error(monkeypatch) -> None:
    import io
    import urllib.error
    import urllib.request

    from kaban.publishing.telegram import TelegramClient

    attempts = 0

    def fake_urlopen(req, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false,"description":"chat not found"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    try:
        TelegramClient("token", retries=3).get_chat("-100123")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Ожидалась RuntimeError")

    assert attempts == 1
    assert "Telegram HTTP 400" in message
    assert "chat not found" in message


def test_telegram_client_sends_media_group_with_caption(tmp_path: Path, monkeypatch) -> None:
    import json
    import urllib.request

    from kaban.publishing.telegram import TelegramClient

    first = tmp_path / "one.jpg"
    second = tmp_path / "two.jpg"
    first.write_bytes(b"one-bytes")
    second.write_bytes(b"two-bytes")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {"ok": True, "result": [{"message_id": 1}, {"message_id": 2}]}
            ).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["content_type"] = req.headers.get("Content-type")
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = TelegramClient("token", retries=0).send_media_group(
        "-100123",
        [first, second],
        caption="<b>Daily</b>",
    )

    assert result == [{"message_id": 1}, {"message_id": 2}]
    assert captured["url"].endswith("/sendMediaGroup")
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    body = captured["body"]
    assert b"attach://file0" in body
    assert b"attach://file1" in body
    assert b"<b>Daily</b>" in body
    assert b'"parse_mode": "HTML"' in body
    assert b"one-bytes" in body
    assert b"two-bytes" in body


def test_caelus_publisher_uses_kaban_telegram_transport() -> None:
    import publish_telegram
    from kaban.publishing.telegram import TelegramClient, multipart_body

    assert publish_telegram.TelegramClient is TelegramClient
    assert publish_telegram.multipart_body is multipart_body


def test_send_message_network_timeout_is_ambiguous_and_not_retried(monkeypatch) -> None:
    import urllib.error
    import urllib.request
    from kaban.publishing.telegram import AmbiguousTelegramError, TelegramClient

    attempts = 0
    def fake_urlopen(req, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.URLError("timeout after request began")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with __import__("pytest").raises(AmbiguousTelegramError):
        TelegramClient("token", retries=3).send_message("-100123", "hello")
    assert attempts == 1


def test_send_message_http_error_is_definitive(monkeypatch) -> None:
    import io
    import urllib.error
    import urllib.request
    from kaban.publishing.telegram import DefinitiveTelegramError, TelegramClient

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", None, io.BytesIO(b'{"ok":false,"description":"bad chat"}'))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with __import__("pytest").raises(DefinitiveTelegramError):
        TelegramClient("token", retries=3).send_message("-100123", "hello")
