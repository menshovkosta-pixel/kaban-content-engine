from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil


class BootstrapError(RuntimeError):
    """Bootstrap остановлен, чтобы не повредить persistent host data."""


@dataclass(frozen=True)
class BootstrapResult:
    copied: int
    skipped_existing: int


def _validate_source(source: Path) -> None:
    if not source.exists():
        raise BootstrapError(f"Bootstrap source не найден: {source}")
    if not source.is_dir():
        raise BootstrapError(f"Bootstrap source должен быть директорией: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise BootstrapError(f"символическая ссылка запрещена в bootstrap source: {path}")


def bootstrap_generated(source: Path, destination: Path, *, merge: bool = False) -> BootstrapResult:
    source = Path(source)
    destination = Path(destination)
    _validate_source(source)

    if destination.is_symlink():
        raise BootstrapError(f"destination содержит символическую ссылку: {destination}")
    if destination.exists() and not destination.is_dir():
        raise BootstrapError(f"Destination должен быть директорией: {destination}")
    if destination.exists() and any(destination.iterdir()) and not merge:
        raise BootstrapError(f"Destination не пуст: {destination}; используйте --merge явно")

    entries = sorted(source.rglob("*"))
    # Сначала проверяем весь target tree, чтобы ошибка не оставила частичный merge.
    for src in entries:
        dst = destination / src.relative_to(source)
        if dst.is_symlink():
            raise BootstrapError(f"destination содержит символическую ссылку: {dst}")
        if not dst.exists():
            continue
        if src.is_dir() and not dst.is_dir():
            raise BootstrapError(f"конфликт типов: source directory и destination file: {dst}")
        if src.is_file() and dst.is_dir():
            raise BootstrapError(f"конфликт типов: source file и destination directory: {dst}")

    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0

    for src in entries:
        relative = src.relative_to(source)
        dst = destination / relative
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if dst.exists():
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    return BootstrapResult(copied=copied, skipped_existing=skipped)


def _parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Безопасный bootstrap shipped generated/ в persistent data/")
    parser.add_argument("--source", type=Path, default=repo_root / "generated")
    parser.add_argument("--destination", type=Path, default=repo_root / "data" / "generated")
    parser.add_argument("--merge", action="store_true", help="Копировать только отсутствующие файлы; host data не перезаписывать")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = bootstrap_generated(args.source, args.destination, merge=args.merge)
    except BootstrapError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Bootstrap completed: copied={result.copied}, skipped_existing={result.skipped_existing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
