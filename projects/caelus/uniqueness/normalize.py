from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    """Нормализует текст для устойчивого сравнения без изменения исходного контента."""
    text = unicodedata.normalize("NFKC", str(text)).casefold().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def tokens(text: str) -> tuple[str, ...]:
    return tuple(part for part in normalize_text(text).split() if len(part) > 1)


def char_ngrams(text: str, n: int = 4) -> set[str]:
    compact = f" {normalize_text(text)} "
    if len(compact) <= n:
        return {compact} if compact.strip() else set()
    return {compact[i:i + n] for i in range(len(compact) - n + 1)}
