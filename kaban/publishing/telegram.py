from __future__ import annotations

import json
import mimetypes
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class TelegramTransportError(RuntimeError):
    """Базовая ошибка Telegram transport."""


class DefinitiveTelegramError(TelegramTransportError):
    """Telegram явно подтвердил отказ; повтор не создаёт риск дубля."""


class AmbiguousTelegramError(TelegramTransportError):
    """Результат запроса неизвестен; автоматический повтор может создать дубль."""


def multipart_body(fields: dict[str, str], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    """Собирает multipart/form-data тело для загрузки файлов в Telegram API."""
    boundary = "----KABANBoundary7MA4YWxkTrZu0gW"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    for field_name, path in files:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {ctype}\r\n\r\n".encode())
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class TelegramClient:
    """Минимальный универсальный клиент Telegram Bot API для KABAN."""

    def __init__(self, token: str, timeout: int = 300, retries: int = 2):
        self.base = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout
        self.retries = retries

    def _urlopen(self, req: urllib.request.Request, *, allow_retry: bool = True):
        max_retries = self.retries if allow_retry else 0
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 2):
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError:
                # HTTP-ответ получен однозначно, поэтому сеть не ретраим.
                raise
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_exc = exc
                if attempt > max_retries:
                    raise
                wait = 2 * attempt
                print(f"[WARN] Сетевая ошибка Telegram, повтор {attempt}/{max_retries} через {wait} с...")
                time.sleep(wait)
        assert last_exc is not None
        raise last_exc

    def _request_json(self, method: str, payload: dict[str, Any], *, side_effect: bool = False) -> Any:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/{method}",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self._urlopen(req, allow_retry=not side_effect) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise DefinitiveTelegramError(f"Telegram HTTP {exc.code}: {details}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise AmbiguousTelegramError(f"Сетевая ошибка Telegram после начала запроса: {exc}") from exc
        if not data.get("ok"):
            raise DefinitiveTelegramError(f"Telegram API error: {data}")
        return data["result"]

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        # Side-effect запрос принципиально не ретраится на сетевой неопределённости.
        return self._request_json(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            side_effect=True,
        )

    def send_media_group(
        self,
        chat_id: str,
        paths: list[Path],
        caption: str | None = None,
    ) -> list[dict[str, Any]]:
        media: list[dict[str, Any]] = []
        files: list[tuple[str, Path]] = []
        for idx, path in enumerate(paths):
            field = f"file{idx}"
            item: dict[str, Any] = {"type": "photo", "media": f"attach://{field}"}
            if idx == 0 and caption:
                item["caption"] = caption
                item["parse_mode"] = "HTML"
            media.append(item)
            files.append((field, path))

        body, content_type = multipart_body(
            {"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
            files,
        )
        req = urllib.request.Request(
            f"{self.base}/sendMediaGroup",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with self._urlopen(req, allow_retry=False) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise DefinitiveTelegramError(f"Telegram HTTP {exc.code}: {details}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise AmbiguousTelegramError(f"Сетевая ошибка Telegram после начала запроса: {exc}") from exc
        if not data.get("ok"):
            raise DefinitiveTelegramError(f"Telegram API error: {data}")
        return data["result"]

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        # Read-only запрос можно безопасно ретраить.
        return self._request_json("getChat", {"chat_id": chat_id}, side_effect=False)

