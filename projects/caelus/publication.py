from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from kaban.publishing.telegram import TelegramClient, multipart_body
from kaban.cloud.publication import (
    ManualReconciliationRequired, checkpoint_client_from_env, request_fingerprint, run_checkpointed_step,
)
from kaban.storage import content_hash, load_json, write_json
from projects.caelus.domain import SIGN_ORDER

from projects.caelus.config import (
    ROOT,
    load_dotenv as load_project_dotenv,
    uniqueness_hard_threshold,
    uniqueness_history_days,
    uniqueness_max_regeneration_attempts,
    uniqueness_warning_threshold,
)
from projects.caelus.storage import append_publication_event, generated_root
from projects.caelus.quality import validate_content
from projects.caelus.uniqueness.rules import UniquenessRules
from projects.caelus.validators import get_field, has_errors


GLYPHS = {
    "aries": "♈", "taurus": "♉", "gemini": "♊", "cancer": "♋",
    "leo": "♌", "virgo": "♍", "libra": "♎", "scorpio": "♏",
    "sagittarius": "♐", "capricorn": "♑", "aquarius": "♒", "pisces": "♓",
}

LABELS = {
    "ru": {
        "title": "Прогноз CAELUS",
        "relationships": "Отношения",
        "work_money": "Работа и деньги",
        "advice": "Совет дня",
    },
    "en": {
        "title": "CAELUS Forecast",
        "relationships": "Relationships",
        "work_money": "Work & money",
        "advice": "Daily note",
    },
}


def run_telegram_publisher(
    day: str,
    language: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, str]:
    """Запускает CAELUS publication strategy отдельным процессом."""
    load_project_dotenv()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    cmd = [sys.executable, "-m", "projects.caelus.publication", "--date", day, "--language", language]
    if dry_run:
        cmd.append("--dry-run")
    if force:
        cmd.append("--force")
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    return result.returncode, result.stdout.strip()


def validate_approved(base: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    content_path = base / "content.json"
    status_path = base / "status.json"

    content = load_json(content_path)
    if not content:
        raise RuntimeError(f"Не найден контент: {content_path}")

    status = load_json(status_path, {}) or {}
    if status.get("state") != "approved":
        raise RuntimeError(
            "Публикация запрещена: комплект не имеет статуса approved. "
            "Сначала откройте Review Console и нажмите Approve All."
        )

    current_hash = content_hash(content)
    approved_hash = status.get("content_hash")
    if approved_hash != current_hash:
        raise RuntimeError(
            "Публикация запрещена: content.json изменился после approval. "
            "Откройте Review Console, проверьте изменения и подтвердите комплект заново."
        )

    try:
        target = datetime.strptime(str(content.get("iso_date")), "%Y-%m-%d").date()
        rules = UniquenessRules(
            history_days=uniqueness_history_days(),
            warning_threshold=uniqueness_warning_threshold(),
            hard_threshold=uniqueness_hard_threshold(),
            max_regeneration_attempts=uniqueness_max_regeneration_attempts(),
        ).validate()
        issues, _ = validate_content(
            content,
            generated_dir=generated_root(),
            language=str(content.get("language") or base.name),
            target=target,
            rules=rules,
        )
    except Exception as exc:
        raise RuntimeError(f"Не удалось выполнить финальную проверку уникальности: {exc}") from exc
    if has_errors(issues):
        messages = "; ".join(str(x["message"]) for x in issues if x.get("severity") == "error")
        raise RuntimeError(f"Публикация запрещена: content dataset не прошёл валидацию/уникальность: {messages}")

    return content, status, current_hash


def validate_cards(base: Path) -> list[Path]:
    cards_dir = base / "cards"
    cards = [cards_dir / f"caelus_{sign}.png" for sign in SIGN_ORDER]
    missing = [p.name for p in cards if not p.is_file()]
    if missing:
        raise RuntimeError("Не найдены карточки: " + ", ".join(missing))
    return cards


def html_sign_block(sign: str, item: dict[str, Any], language: str) -> str:
    labels = LABELS[language]
    name = html.escape(str(item["name"]))
    overview = html.escape(get_field(item, "general"))
    relationships = html.escape(get_field(item, "love"))
    work_money = html.escape(get_field(item, "career_money"))
    advice = html.escape(get_field(item, "advice"))

    return (
        f"{GLYPHS[sign]} <b>{name}</b>\n\n"
        f"{overview}\n\n"
        f"❤️ <b>{labels['relationships']}</b>\n{relationships}\n\n"
        f"💼 <b>{labels['work_money']}</b>\n{work_money}\n\n"
        f"✨ <b>{labels['advice']}</b>\n{advice}"
    )


def build_text_batches(content: dict[str, Any], max_chars: int = 3600) -> list[str]:
    language = content["language"]
    labels = LABELS[language]
    header = f"<b>{html.escape(labels['title'])} · {html.escape(content['date'])}</b>\n\n"

    batches: list[str] = []
    current = header
    for sign in SIGN_ORDER:
        block = html_sign_block(sign, content["signs"][sign], language)
        separator = "\n\n———\n\n"
        candidate = current + block + separator
        if len(candidate) > max_chars and current != header:
            batches.append(current.rstrip("\n— "))
            current = header + block + separator
        else:
            current = candidate

    if current.strip() != header.strip():
        batches.append(current.rstrip("\n— "))
    return batches


def build_media_groups(cards: list[Path]) -> list[list[Path]]:
    # Telegram sendMediaGroup принимает от 2 до 10 элементов.
    return [cards[:6], cards[6:12]]


def load_dotenv(path: Path) -> None:
    """Минимальная загрузка .env без дополнительной зависимости."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def optimize_cards_for_telegram(base: Path, cards: list[Path], quality: int = 90) -> list[Path]:
    """
    Создаёт облегчённые JPEG-копии для Telegram.
    Оригинальные PNG не изменяются.
    """
    media_dir = base / "telegram_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    optimized: list[Path] = []

    for src in cards:
        dst = media_dir / (src.stem + ".jpg")
        # Пересобираем, если исходник новее или JPEG отсутствует.
        if (not dst.exists()) or src.stat().st_mtime > dst.stat().st_mtime:
            with Image.open(src) as im:
                rgb = im.convert("RGB")
                rgb.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
        optimized.append(dst)

    return optimized


def masked_chat_id(chat_id: str) -> str:
    if len(chat_id) <= 6:
        return "***"
    return chat_id[:3] + "***" + chat_id[-3:]


def make_plan(content: dict[str, Any], cards: list[Path]) -> dict[str, Any]:
    groups = build_media_groups(cards)
    texts = build_text_batches(content)
    return {
        "date": content["iso_date"],
        "language": content["language"],
        "media_groups": [
            {"count": len(group), "files": [p.name for p in group]}
            for group in groups
        ],
        "text_batches": [
            {"index": i, "characters": len(text)}
            for i, text in enumerate(texts, start=1)
        ],
        "order": "cards first, then full forecast text",
    }




def write_publication_journal(path: Path, payload: dict[str, Any]) -> None:
    current = load_json(path, {}) or {}
    merged = dict(payload)
    if isinstance(current, dict) and "checkpoints" in current and "checkpoints" not in merged:
        merged["checkpoints"] = current["checkpoints"]
    write_json(path, merged)


def publish_media_step(*, checkpoints, publication_run_id, telegram, chat_id: str, group_index: int, paths: list[Path], caption: str | None, approved_hash: str):
    step_key = f"media:{group_index}"
    fingerprint = request_fingerprint({
        "project_id": "caelus",
        "step_key": step_key,
        "approved_hash": approved_hash,
        "chat_id": str(chat_id),
        "files": [path.name for path in paths],
        "caption": caption,
    })
    return run_checkpointed_step(
        checkpoints, publication_run_id, step_key, fingerprint,
        lambda: telegram.send_media_group(chat_id, paths, caption=caption),
        external_ids=lambda result: {"message_ids": [item.get("message_id") for item in result]},
    )


def publish_text_step(*, checkpoints, publication_run_id, telegram, chat_id: str, batch_index: int, text: str, approved_hash: str):
    step_key = f"text:{batch_index}"
    fingerprint = request_fingerprint({
        "project_id": "caelus",
        "step_key": step_key,
        "approved_hash": approved_hash,
        "chat_id": str(chat_id),
        "text": text,
    })
    return run_checkpointed_step(
        checkpoints, publication_run_id, step_key, fingerprint,
        lambda: telegram.send_message(chat_id, text),
        external_ids=lambda result: {"message_id": result.get("message_id")},
    )

def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="CAELUS: публикация approved-комплекта в Telegram")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--language", choices=["ru", "en"], default="ru")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не отправлять; только проверить и сохранить план")
    parser.add_argument("--force", action="store_true", help="Разрешить повторную публикацию того же approved-комплекта")
    parser.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID"), help="Числовой Telegram chat/channel ID, например -100xxxxxxxxxx")
    parser.add_argument("--timeout", type=int, default=300, help="Таймаут одного Telegram-запроса в секундах (по умолчанию 300)")
    parser.add_argument(
        "--start-album",
        type=int,
        choices=[1, 2],
        default=1,
        help="Начать отправку с указанного альбома. Используйте 2, если первый альбом уже был успешно отправлен.",
    )
    args = parser.parse_args()

    base = generated_root() / args.date / args.language
    content, status, approved_hash = validate_approved(base)
    cards = validate_cards(base)

    journal_path = base / "publication.json"
    old_journal = load_json(journal_path, {}) or {}
    if old_journal.get("state") == "published" and not args.force:
        raise SystemExit(
            "Этот день уже опубликован. Повторная публикация из обычного workflow заблокирована. "
            "Для намеренной повторной отправки из CLI используйте --force."
        )
    if (
        old_journal.get("content_hash")
        and old_journal.get("content_hash") != approved_hash
        and (old_journal.get("media") or old_journal.get("text"))
        and not args.force
    ):
        raise SystemExit(
            "Для этого дня уже есть частично отправленные Telegram-сообщения с другим content_hash. "
            "Автоматический сброс прогресса запрещён, чтобы не создать дубли."
        )

    plan = make_plan(content, cards)
    plan_path = base / "telegram_publish_plan.json"
    write_json(plan_path, plan)

    print("CAELUS Telegram publish plan")
    print(f"  Дата:       {content['iso_date']}")
    print(f"  Язык:       {content['language']}")
    print(f"  Альбомы:    {', '.join(str(x['count']) for x in plan['media_groups'])} карточек")
    print(f"  Текстов:    {len(plan['text_batches'])}")
    print(f"  План:       {plan_path}")

    if args.dry_run:
        print("\n[DRY-RUN] Ничего не отправлено в Telegram.")
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = args.chat_id
    if not token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN. Используйте переменную окружения или --dry-run.")
    if not chat_id:
        raise SystemExit(
            "Не задан TELEGRAM_CHAT_ID. Создайте .env рядом с publish_telegram.py и укажите "
            "TELEGRAM_CHAT_ID=-100xxxxxxxxxx."
        )
    chat_id = str(chat_id).strip()
    if not (chat_id.startswith("-100") and chat_id[1:].isdigit()):
        raise SystemExit("TELEGRAM_CHAT_ID должен быть числовым ID канала в формате -100xxxxxxxxxx.")

    # Журнал переводится в PUBLISHING до любых сетевых действий. Так даже
    # ошибка getChat/оптимизации получает явный FAILED и попадает в историю.
    progress = {} if args.force else (load_json(journal_path, {}) or {})
    resumable_states = {"publishing", "partially_published", "failed", "unknown_delivery"}
    if progress.get("content_hash") != approved_hash or progress.get("state") not in resumable_states:
        progress = {
            "state": "publishing",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": approved_hash,
            "approved_at": status.get("approved_at"),
            "date": content["iso_date"],
            "language": content["language"],
            "chat_id": masked_chat_id(chat_id),
            "media": [],
            "text": [],
            "attempt": 1,
        }
        append_publication_event(base, "publication_started", state="publishing", content_hash=approved_hash)
    else:
        progress["state"] = "publishing"
        progress["attempt"] = int(progress.get("attempt", 1)) + 1
        progress["resumed_at"] = datetime.now(timezone.utc).isoformat()
        progress.pop("last_error", None)
        append_publication_event(base, "publication_resumed", state="publishing", content_hash=approved_hash)
    write_publication_journal(journal_path, progress)

    sent_media: list[dict[str, Any]] = list(progress.get("media", []))
    sent_text: list[dict[str, Any]] = list(progress.get("text", []))
    sent_groups = {int(x.get("group")) for x in sent_media if x.get("group") is not None}
    sent_batches = {int(x.get("batch")) for x in sent_text if x.get("batch") is not None}
    started_at = progress.get("started_at") or datetime.now(timezone.utc).isoformat()

    try:
        telegram_cards = optimize_cards_for_telegram(base, cards)
        original_mb = sum(p.stat().st_size for p in cards) / 1024 / 1024
        optimized_mb = sum(p.stat().st_size for p in telegram_cards) / 1024 / 1024
        print(f"  Telegram media: {optimized_mb:.1f} MB вместо {original_mb:.1f} MB PNG")

        client = TelegramClient(token, timeout=args.timeout)
        try:
            chat_info = client.get_chat(chat_id)
        except Exception as exc:
            raise RuntimeError(f"Не удалось открыть Telegram-канал {masked_chat_id(chat_id)}: {exc}") from exc
        resolved_chat_id = str(chat_info.get("id"))
        resolved_title = chat_info.get("title") or chat_info.get("username") or "Telegram chat"
        progress["chat_id"] = masked_chat_id(resolved_chat_id)
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_publication_journal(journal_path, progress)
        print(f"  Telegram target: {resolved_title} ({masked_chat_id(resolved_chat_id)})")

        media_groups = build_media_groups(telegram_cards)
        text_batches = build_text_batches(content)
        publication_key = f"telegram:{content['iso_date']}:{content['language']}:{approved_hash}"
        if args.force and os.getenv("KABAN_EXECUTION_ID"):
            publication_key += f":force:{os.environ['KABAN_EXECUTION_ID']}"
        checkpoint_client, publication_run_id = checkpoint_client_from_env(
            journal_path=journal_path, project_id="caelus", publication_key=publication_key,
        )

        # Карточки отправляем двумя сбалансированными альбомами: 6 + 6.
        for idx, group in enumerate(media_groups, start=1):
            if idx < args.start_album:
                print(f"[SKIP] Альбом {idx}: пропущен по --start-album {args.start_album}")
                continue
            group_mb = sum(p.stat().st_size for p in group) / 1024 / 1024
            caption = None
            if idx == 1:
                title = LABELS[args.language]["title"]
                caption = f"<b>{html.escape(title)} · {html.escape(content['date'])}</b>"
            print(f"[SEND] Альбом {idx}/{len(media_groups)}: {len(group)} карточек, {group_mb:.1f} MB...")
            result = publish_media_step(
                checkpoints=checkpoint_client,
                publication_run_id=publication_run_id,
                telegram=client,
                chat_id=resolved_chat_id,
                group_index=idx,
                paths=group,
                caption=caption,
                approved_hash=approved_hash,
            )
            if result is None:
                print(f"[SKIP] Альбом {idx}: canonical checkpoint уже sent")
                continue
            sent_media.append({
                "group": idx,
                "message_ids": [item.get("message_id") for item in result],
                "files": [p.name for p in group],
            })
            progress["media"] = sent_media
            progress["updated_at"] = datetime.now(timezone.utc).isoformat()
            write_publication_journal(journal_path, progress)
            append_publication_event(base, "media_group_sent", group=idx, message_count=len(result))
            print(f"[OK] Альбом {idx}: {len(group)} карточек")

        for idx, text in enumerate(text_batches, start=1):
            result = publish_text_step(
                checkpoints=checkpoint_client,
                publication_run_id=publication_run_id,
                telegram=client,
                chat_id=resolved_chat_id,
                batch_index=idx,
                text=text,
                approved_hash=approved_hash,
            )
            if result is None:
                print(f"[SKIP] Текст {idx}: canonical checkpoint уже sent")
                continue
            sent_text.append({"batch": idx, "message_id": result.get("message_id")})
            progress["text"] = sent_text
            progress["updated_at"] = datetime.now(timezone.utc).isoformat()
            write_publication_journal(journal_path, progress)
            append_publication_event(base, "text_batch_sent", batch=idx, message_id=result.get("message_id"))
            print(f"[OK] Текст {idx}/{len(text_batches)}")

        journal = {
            "state": "published",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started_at,
            "content_hash": approved_hash,
            "approved_at": status.get("approved_at"),
            "date": content["iso_date"],
            "language": content["language"],
            "chat_id": masked_chat_id(resolved_chat_id),
            "media": sent_media,
            "text": sent_text,
            "attempt": progress.get("attempt", 1),
        }
        write_publication_journal(journal_path, journal)
        append_publication_event(base, "publication_completed", state="published", content_hash=approved_hash)
        print(f"\nГотово. Журнал публикации: {journal_path}")
    except Exception as exc:
        partial = bool(sent_media or sent_text)
        if isinstance(exc, ManualReconciliationRequired):
            progress["state"] = "unknown_delivery"
        else:
            progress["state"] = "partially_published" if partial else "failed"
        progress["media"] = sent_media
        progress["text"] = sent_text
        progress["failed_at"] = datetime.now(timezone.utc).isoformat()
        progress["last_error"] = str(exc)
        write_publication_journal(journal_path, progress)
        append_publication_event(
            base,
            "publication_failed",
            state=progress["state"],
            error=str(exc),
            sent_media_groups=len(sent_media),
            sent_text_batches=len(sent_text),
        )
        raise


if __name__ == "__main__":
    try:
        main()
    except ManualReconciliationRequired as exc:
        print(f"[MANUAL_RECONCILIATION] {exc}", file=sys.stderr)
        raise SystemExit(3)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
