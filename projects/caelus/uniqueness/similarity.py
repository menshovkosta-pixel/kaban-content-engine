from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from math import sqrt
from collections import Counter

from .normalize import char_ngrams, normalize_text, tokens


@dataclass(frozen=True)
class SimilarityResult:
    score: float
    sequence: float
    token_jaccard: float
    token_cosine: float
    char_ngram: float
    exact: bool


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _cosine(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    a = Counter(left)
    b = Counter(right)
    common = set(a) & set(b)
    dot = sum(a[key] * b[key] for key in common)
    norm_a = sqrt(sum(value * value for value in a.values()))
    norm_b = sqrt(sum(value * value for value in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def compare_texts(left: str, right: str) -> SimilarityResult:
    a = normalize_text(left)
    b = normalize_text(right)
    if not a or not b:
        return SimilarityResult(0.0, 0.0, 0.0, 0.0, 0.0, False)
    exact = a == b
    sequence = SequenceMatcher(None, a, b).ratio()
    left_tokens = tokens(a)
    right_tokens = tokens(b)
    token_jaccard = _jaccard(set(left_tokens), set(right_tokens))
    token_cosine = _cosine(left_tokens, right_tokens)
    char_ngram = _jaccard(char_ngrams(a), char_ngrams(b))

    # Берём наиболее сильный сигнал, но не позволяем одному случайному общему набору
    # коротких слов полностью определять результат. Exact duplicate всегда 100%.
    lexical_mix = 0.45 * sequence + 0.30 * token_cosine + 0.25 * char_ngram
    score = 1.0 if exact else max(lexical_mix, 0.90 * sequence, 0.86 * token_cosine, 0.80 * token_jaccard)
    return SimilarityResult(min(score, 1.0), sequence, token_jaccard, token_cosine, char_ngram, exact)
