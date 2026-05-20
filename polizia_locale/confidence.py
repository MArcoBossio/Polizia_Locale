"""Confidence engine multi-fonte per risultati di Polizia Locale."""
from __future__ import annotations

import re

from .indicepa import is_pl_specific_email
from .normalization import canonical_commune_name, normalize_text
from urllib.parse import urlparse


SOURCE_BASE = {
    "IndicePA": 0.93,
    "IndicePA-AOO": 0.91,
    "IndicePA-Unione": 0.92,
    "WebScraping+Verifica": 0.88,
    "ScrapingSitoComune": 0.78,
    "WebSearch": 0.70,
    "IndicePA-Comune": 0.54,
    "IndicePA-Comune (fallback)": 0.54,
    "IndicePA-Comune (fallback auto post-scraping)": 0.52,
    "NON TROVATO": 0.0,
    "NON TROVATO (scrape-limit)": 0.0,
}

SOURCE_BONUS = {
    "context_polizia": 0.10,
    "context_fuzzy_polizia": 0.12,
    "context_non_pl": -0.08,
    "local_part_pl": 0.10,
    "dom_parent_pl": 0.08,
    "dom_heading_pl": 0.05,
    "sibling_context_pl": 0.09,
    "anchor_context_pl": 0.08,
    "section_context_pl": 0.07,
    "page_html": 0.02,
    "js_render": 0.05,
    "ocr": 0.04,
    "pdf": 0.08,
    "verified": 0.08,
    "site_match": 0.04,
}


def _split_sources(text: str) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in re.split(r"\s*\+\s*|\s*\|\s*", text) if part.strip()]


def _source_base_score(fonte: str) -> float:
    parts = _split_sources(fonte)
    if not parts:
        parts = [fonte.strip()]
    best = 0.35
    for part in parts:
        if not part:
            continue
        for key, score in SOURCE_BASE.items():
            if key and key.lower() in part.lower():
                best = max(best, score)
    return best


def _score_email_value(email: str) -> float:
    if not email or "@" not in email:
        return 0.0
    if is_pl_specific_email(email):
        return 0.06
    local = email.split("@", 1)[0].lower()
    if any(token in local for token in ("polizia", "vigili", "comando", "pm", "pl")):
        return 0.03
    return 0.0


def _domain_root(domain: str) -> str:
    parts = [part for part in domain.lower().split(".") if part]
    if len(parts) >= 3 and parts[-2:] in (["gov", "it"], ["com", "it"]):
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return domain.lower()


def score_row(row: dict) -> tuple[float, str]:
    fonte = (row.get("fonte") or "").strip()
    matched_by = row.get("matched_by") or ""
    pec = (row.get("pec") or "").strip()
    email = (row.get("email") or row.get("mail") or "").strip()
    sito = (row.get("sito") or "").strip()
    descrizione = normalize_text(row.get("descrizione_uo") or "")

    score = _source_base_score(fonte)
    reasons: list[str] = [f"source:{fonte or 'unknown'}"]

    if pec:
        score += 0.03
        reasons.append("has_pec")
    if email:
        score += 0.03
        reasons.append("has_email")
        score += _score_email_value(email)
        if sito and "@" in email:
            email_domain = email.split("@", 1)[1]
            site_host = urlparse(sito if sito.startswith(("http://", "https://")) else "https://" + sito).netloc.lower().lstrip("www.")
            if site_host:
                if email_domain == site_host or email_domain.endswith("." + site_host) or _domain_root(email_domain) == _domain_root(site_host):
                    score += SOURCE_BONUS["site_match"]
                    reasons.append("site_match")

    if sito:
        score += 0.02
        reasons.append("has_site")

    if canonical_commune_name(row.get("comune") or row.get("denominazione_ente") or ""):
        score += 0.01

    if any(token in descrizione for token in ("polizia locale", "polizia municipale", "vigili urbani", "comando")):
        score += 0.04
        reasons.append("pl_description")

    for token in _split_sources(matched_by):
        bonus = SOURCE_BONUS.get(token, 0.0)
        if bonus:
            score += bonus
            reasons.append(token)

    if "verified" in fonte.lower() or "verificato" in fonte.lower():
        score += SOURCE_BONUS["verified"]
        reasons.append("verified")

    score = max(0.0, min(score, 0.99))
    return score, " | ".join(dict.fromkeys(reasons))


def apply_confidence(rows: list[dict]) -> list[dict]:
    for row in rows:
        score, reasons = score_row(row)
        row["confidence"] = round(score, 3)
        existing = row.get("matched_by", "")
        row["matched_by"] = " | ".join(part for part in [existing, reasons] if part)
    return rows