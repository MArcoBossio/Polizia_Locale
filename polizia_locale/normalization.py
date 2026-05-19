"""Normalizzazione e matching fuzzy per nomi di comuni e testi istituzionali."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

try:
    from rapidfuzz.fuzz import ratio as _rapid_ratio
except Exception:  # pragma: no cover - optional dependency
    _rapid_ratio = None


_BILINGUAL_ALIASES = {
    "bozen": "bolzano",
    "leifers": "laives",
    "brixen": "bressanone",
    "meran": "merano",
    "st ulrich": "ortisei",
    "bruneck": "brunico",
    "sterzing": "vipiteno",
    "glurns": "glorenza",
    "mals": "malles",
}


def strip_accents(text: str) -> str:
    if not isinstance(text, str):
        return ""
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = strip_accents(text).lower()
    text = text.replace("'", " ").replace("/", " ").replace("-", " ")
    text = re.sub(r"\b(s)\.(?=\s|$)", r"\1an", text)
    text = re.sub(r"\b(st)\.(?=\s|$)", r"st", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_commune_name(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    parts = [part for part in normalized.split() if part not in {"di", "del", "della", "dello", "dei", "degli", "delle"}]
    canonical = " ".join(parts)
    for alias, target in _BILINGUAL_ALIASES.items():
        canonical = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", target, canonical)
    canonical_parts: list[str] = []
    for part in canonical.split():
        if part not in canonical_parts:
            canonical_parts.append(part)
    canonical = " ".join(canonical_parts)
    canonical = re.sub(r"\s+", " ", canonical).strip()
    return canonical


def similarity(a: str, b: str) -> float:
    left = canonical_commune_name(a)
    right = canonical_commune_name(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if _rapid_ratio is not None:
        return _rapid_ratio(left, right) / 100.0
    return SequenceMatcher(None, left, right).ratio()


def is_close_match(a: str, b: str, threshold: float = 0.9) -> bool:
    return similarity(a, b) >= threshold


def commune_variants(text: str) -> list[str]:
    canonical = canonical_commune_name(text)
    if not canonical:
        return []
    variants = [canonical]
    for alias, target in _BILINGUAL_ALIASES.items():
        if alias in canonical:
            variants.append(canonical.replace(alias, target))
        if target in canonical:
            variants.append(canonical.replace(target, alias))
    return list(dict.fromkeys(variant for variant in variants if variant))
