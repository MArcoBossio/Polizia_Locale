"""Estrazione di email dai PDF linkati nelle pagine comunali.

Alcuni Comuni espongono la mail della Polizia Locale solo in PDF (ordinanze,
organigrammi, contatti uffici). Questo modulo scarica un PDF, ne estrae il
testo con `pypdf` e cerca le mail.
"""
from __future__ import annotations

import io
import re

import requests

from pypdf import PdfReader

from .scraper import EMAIL_RE


def _extract_emails_with_line_context(text: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in text.splitlines()]
    out: list[tuple[str, str]] = []
    for idx, line in enumerate(lines):
        if "@" not in line:
            continue
        for m in EMAIL_RE.finditer(line):
            start = max(0, idx - 2)
            end = min(len(lines), idx + 3)
            context = " | ".join(part for part in lines[start:end] if part)
            out.append((m.group(0), context or line))
    return out


def extract_emails_from_pdf_url(
    session: requests.Session, url: str, timeout: int = 8, max_size_bytes: int = 5_000_000
) -> list[tuple[str, str]]:
    """Scarica un PDF e ritorna lista (email, contesto_breve).
    Limita a `max_size_bytes` (default 5MB) per evitare download enormi.
    """
    try:
        r = session.get(url, timeout=timeout, stream=True)
        if r.status_code != 200:
            return []
        ct = r.headers.get("Content-Type", "").lower()
        if "pdf" not in ct and not url.lower().endswith(".pdf"):
            return []
        content = b""
        for chunk in r.iter_content(chunk_size=64 * 1024):
            content += chunk
            if len(content) > max_size_bytes:
                return []  # PDF troppo grande, salta
    except Exception:
        return []

    try:
        reader = PdfReader(io.BytesIO(content))
        # limito a max 30 pagine per evitare PDF enormi (es. piani regolatori)
        pages = reader.pages[:30]
        text = " \n ".join(p.extract_text() or "" for p in pages)
    except Exception:
        return []

    if len(text.strip()) < 40:
        try:
            from pdf2image import convert_from_bytes
            import pytesseract

            ocr_chunks: list[str] = []
            for image in convert_from_bytes(content, first_page=1, last_page=4):
                try:
                    ocr_chunks.append(pytesseract.image_to_string(image, lang="ita"))
                except Exception:
                    continue
            if ocr_chunks:
                text = text + " \n " + " \n ".join(ocr_chunks)
        except Exception:
            pass

    return _extract_emails_with_line_context(text)


def find_pdf_links(html: str, base: str, limit: int = 5) -> list[str]:
    """Estrae fino a `limit` URL di PDF dalla pagina, prioritizzando quelli
    che hanno nel nome o nel testo del link parole chiave PL.
    """
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    scored: list[tuple[str, int]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").lower()
        if not href.lower().endswith(".pdf") and ".pdf" not in href.lower():
            continue
        absu = urljoin(base, href)
        if not absu.startswith("http"):
            continue
        hay = (text + " " + href.lower())
        if any(k in hay for k in ["polizia local", "polizia municipal", "polizia-local", "polizia-municipal", "vigili urbani", "vigili-urbani", "comando polizia", "comando-polizia"]):
            score = 4
        elif any(k in hay for k in ["contatti", "organigramma", "uffici", "rubrica", "directory", "elenco", "responsabili", "telefoni"]):
            score = 2
        elif text or href.lower().endswith(".pdf"):
            score = 1
        else:
            score = 0
        if score:
            scored.append((absu, score))
    # ordina e dedup
    seen: set[str] = set()
    out: list[str] = []
    for url, _ in sorted(scored, key=lambda x: -x[1]):
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def find_pdf_links_broad(html: str, base: str, limit: int = 10) -> list[str]:
    """Estrae un set più ampio di PDF, utile sulle pagine PL o sui nodi già sospetti."""
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    scored: list[tuple[str, int]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").lower()
        if not href.lower().endswith(".pdf") and ".pdf" not in href.lower():
            continue
        absu = urljoin(base, href)
        if not absu.startswith("http"):
            continue
        hay = f"{text} {href.lower()}"
        if any(k in hay for k in ("polizia", "vigili", "comando", "municipale")):
            score = 5
        elif any(k in hay for k in ("contatti", "organigramma", "uffici", "rubrica", "elenco", "responsabili", "telefoni", "directory")):
            score = 3
        else:
            score = 1
        scored.append((absu, score))
    seen: set[str] = set()
    out: list[str] = []
    for url, _score in sorted(scored, key=lambda x: -x[1]):
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out
