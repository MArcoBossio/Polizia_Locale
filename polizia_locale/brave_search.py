"""Ricerca web di mail PL-specifiche tramite Brave Search API.

Brave Search API restituisce risultati Google-like in JSON. È molto più
veloce di Playwright (~0.3s vs ~5s per query) e affidabile.

Configurazione: variabile d'ambiente `BRAVE_API_KEY` (file `.env` o shell).
Free tier: 2.000 query/mese, 1 query/sec (è il limite del piano "free").

Workflow per ogni comune senza match diretto:
  1. Esegue 1-2 query mirate (es. "polizia locale {comune} mail")
  2. Esamina gli snippet dei primi N risultati
  3. Se nessuno snippet contiene mail PL-specifica, scarica le prime 3
     pagine candidate e cerca dentro (HTML + PDF)
"""
from __future__ import annotations

import os
import re
import threading
import time
from contextlib import suppress
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .indicepa import is_pl_specific_email
from .scraper import EMAIL_RE, _is_pec


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _load_env() -> None:
    """Carica .env se presente (senza dipendere da python-dotenv)."""
    for path in (".env", "/app/.env"):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_load_env()


def _build_queries(comune: str, provincia: str) -> list[str]:
    """Query in ordine di specificità."""
    base = comune.strip()
    prov = provincia.strip()
    q_prov = f" ({prov})" if prov else ""
    return [
        f'"polizia locale" {base}{q_prov} mail',
        f'"polizia municipale" {base}{q_prov} email',
        f'polizia locale {base}{q_prov} contatti email',
        f'vigili urbani {base}{q_prov} mail',
    ]


def _harvest_emails(text: str, _domain_ok) -> tuple[set[str], set[str]]:
    pec, mail = set(), set()
    for m in EMAIL_RE.finditer(text):
        email = m.group(0)
        if not is_pl_specific_email(email):
            continue
        if not _domain_ok(email):
            continue
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 80)
        ctx = text[start:end]
        if _is_pec(email, ctx):
            pec.add(email)
        else:
            mail.add(email)
    return pec, mail


class BraveSearchFinder:
    """Client per l'API Brave Search con rate limiting (1 query/sec)."""

    def __init__(self, api_key: str | None = None, rate_limit_per_sec: float = 1.0):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "BRAVE_API_KEY non trovata. Aggiungila a /app/.env o esportala "
                "come variabile d'ambiente."
            )
        self.min_interval = 1.0 / max(0.1, rate_limit_per_sec)
        self._last_call = 0.0
        self._lock = threading.Lock()
        self._sess = requests.Session()
        self._sess.headers.update(
            {
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            }
        )
        # sessione separata per scaricare le pagine HTML/PDF (no API token)
        self._page_sess = requests.Session()
        self._page_sess.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64)"

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()

    def _search(self, query: str, count: int = 8) -> list[dict]:
        self._throttle()
        try:
            r = self._sess.get(
                BRAVE_ENDPOINT,
                params={
                    "q": query,
                    "country": "IT",
                    "search_lang": "it",
                    "count": count,
                    "safesearch": "off",
                },
                timeout=15,
            )
            if r.status_code != 200:
                return []
            return r.json().get("web", {}).get("results", [])
        except Exception:
            return []

    def _fetch_page(self, url: str, timeout: int = 8) -> str:
        try:
            r = self._page_sess.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code != 200:
                return ""
            ct = r.headers.get("Content-Type", "").lower()
            # PDF: estrai testo con pypdf
            if "pdf" in ct or url.lower().endswith(".pdf"):
                if len(r.content) > 5_000_000:
                    return ""
                try:
                    from io import BytesIO
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(r.content))
                    return " ".join(p.extract_text() or "" for p in reader.pages[:30])
                except Exception:
                    return ""
            # HTML
            soup = BeautifulSoup(r.text, "html.parser")
            return soup.get_text(" ", strip=True)
        except Exception:
            return ""

    def search_polizia_locale(
        self,
        comune: str,
        provincia: str = "",
        domain_hint: str = "",
        deep: bool = True,
    ) -> tuple[set[str], set[str], list[str]]:
        """Cerca mail PL-specifiche per un comune.

        Ritorna (pec_set, mail_set, fonti) dove `fonti` è la lista degli URL
        da cui sono state estratte le mail.
        """
        host = domain_hint.lower().lstrip("www.").strip("/") if domain_hint else ""

        def _domain_ok(email: str) -> bool:
            if not host:
                return True
            domain = email.split("@", 1)[1].lower() if "@" in email else ""
            return (
                domain == host
                or domain.endswith("." + host)
                or host.endswith("." + domain)
                # accetta anche PEC su domini "pec.<comune>.<prov>.it"
                or domain.startswith("pec.")
                and host.split(".")[1:] == domain.split(".")[2:]
            )

        pec_all: set[str] = set()
        mail_all: set[str] = set()
        sources: list[str] = []

        for query in _build_queries(comune, provincia):
            results = self._search(query, count=8)
            if not results:
                continue

            # 1) Cerca mail PL-specifiche direttamente negli snippet
            snippet_text = " ".join(
                [(r.get("description") or "") + " " + (r.get("title") or "") for r in results]
            )
            pec, mail = _harvest_emails(snippet_text, _domain_ok)
            if pec or mail:
                pec_all |= pec
                mail_all |= mail
                sources.extend([r["url"] for r in results[:3]])
                break

            # 2) Niente nei snippet: scarica le pagine candidate (max 3)
            if deep:
                candidate_urls: list[str] = []
                for r in results[:5]:
                    url = r.get("url", "")
                    if not url:
                        continue
                    # priorità a pagine del sito del comune o PEC istituzionale
                    if host and host in url.lower():
                        candidate_urls.insert(0, url)
                    else:
                        candidate_urls.append(url)
                for url in candidate_urls[:3]:
                    text = self._fetch_page(url)
                    if not text:
                        continue
                    pec, mail = _harvest_emails(text, _domain_ok)
                    if pec or mail:
                        pec_all |= pec
                        mail_all |= mail
                        sources.append(url)
                        break
            if pec_all or mail_all:
                break

        return pec_all, mail_all, sources

    def close(self) -> None:
        with suppress(Exception):
            self._sess.close()
            self._page_sess.close()
