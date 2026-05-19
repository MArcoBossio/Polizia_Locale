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
import json
from contextlib import suppress
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .indicepa import is_pl_specific_email
from .scraper import EMAIL_RE, _is_pec
from .utils import is_likely_personal_email


GENERIC_LOCAL_PARTS = (
    "info",
    "segreteria",
    "protocollo",
    "ufficio",
    "amministrazione",
    "contatti",
    "contact",
    "help",
    "service",
    "webmaster",
    "noreply",
    "postmaster",
)


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
    """Query in ordine di specificità. Aggiunge varianti per coprire più casi."""
    base = comune.strip()
    prov = provincia.strip()
    q_prov = f" ({prov})" if prov else ""
    primary = [
        f'polizia locale email {base}{q_prov}',
        f'polizia municipale email {base}{q_prov}',
        f'polizia locale mail {base}{q_prov}',
        f'polizia municipale mail {base}{q_prov}',
        f'"polizia locale" {base}{q_prov} mail',
        f'"polizia municipale" {base}{q_prov} email',
        f'polizia locale {base}{q_prov} contatti email',
        f'vigili urbani {base}{q_prov} mail',
    ]
    extras = [
        f'polizia locale {base}{q_prov} contatti',
        f'ufficio polizia municipale {base}{q_prov} contatti',
        f'segreteria comune {base}{q_prov} contatti',
        f'responsabile polizia locale {base}{q_prov} email',
        f'protocollo comune {base}{q_prov} pec',
    ]
    # ordine: primary poi varianti extra
    return primary + extras


def _harvest_emails(text: str, _domain_ok, only_pl_specific: bool = True) -> tuple[set[str], set[str]]:
    pec, mail = set(), set()
    for m in EMAIL_RE.finditer(text):
        email = m.group(0)
        # In strict mode we normally accept only PL-specific local-parts
        # (polizialocale@, vigili@, ...). However, accept generic local-parts
        # like info@ when the surrounding snippet contains clear indicators
        # of Polizia Locale to avoid false negatives.
        if only_pl_specific and not is_pl_specific_email(email):
            snippet_l = text.lower()
            if not any(pk in snippet_l for pk in ("polizia", "vigili", "polizialocale", "poliziamunicipale", "comando")):
                continue
        # scarta indirizzi personali/di provider gratuiti se non PL-specifici
        if is_likely_personal_email(email) and not is_pl_specific_email(email):
            continue
        # se non siamo in modalità strictly PL, scartiamo local-part generiche
        local = email.split("@", 1)[0].lower()
        if not only_pl_specific and not is_pl_specific_email(email):
            # se la local-part e' generica e il contesto non contiene parole PL,
            # scartiamo l'indirizzo
            if any(local == g or local.startswith(g + ".") or local.startswith(g + "_") or local.startswith(g + "-") for g in GENERIC_LOCAL_PARTS):
                if not any(pk in text.lower() for pk in ("polizia", "vigili", "polizialocale", "poliziamunicipale", "comando")):
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

    def _search(self, query: str, count: int = 12) -> list[dict]:
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
        # semplice retry con backoff per transient network error
        backoff = 0.5
        for attempt in range(3):
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
                        text = " ".join(p.extract_text() or "" for p in reader.pages[:30])
                        # if PDF text is empty, try OCR if available
                        if not text.strip():
                            try:
                                from PIL import Image
                                import pytesseract
                                try:
                                    from pdf2image import convert_from_bytes
                                except Exception:
                                    convert_from_bytes = None
                                if convert_from_bytes:
                                    pages = convert_from_bytes(r.content, first_page=1, last_page=3)
                                    ocr_texts = []
                                    for im in pages:
                                        try:
                                            ocr_texts.append(pytesseract.image_to_string(im, lang='ita'))
                                        except Exception:
                                            pass
                                    text = text + " " + " ".join(ocr_texts)
                            except Exception:
                                pass
                        return text
                    except Exception:
                        return ""
                # HTML
                soup = BeautifulSoup(r.text, "html.parser")
                page_text = soup.get_text(" ", strip=True)
                # OCR images found in page (if pytesseract available)
                try:
                    import pytesseract
                    from PIL import Image
                    from io import BytesIO

                    def _open_image_safe(data: bytes) -> "Image.Image":
                        im = Image.open(BytesIO(data))
                        # handle palette images with transparency properly
                        if im.mode == "P" and "transparency" in im.info:
                            im = im.convert("RGBA")
                            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                            im = Image.alpha_composite(bg, im).convert("RGB")
                        elif im.mode != "RGB":
                            im = im.convert("RGB")
                        return im

                    images = []
                    for img in soup.find_all("img"):
                        src = img.get("src")
                        if not src:
                            continue
                        if src.startswith("data:"):
                            # base64 embedded image
                            try:
                                import base64
                                header, b64 = src.split(",", 1)
                                data = base64.b64decode(b64)
                                im = _open_image_safe(data)
                                images.append(im)
                            except Exception:
                                continue
                        else:
                            # relative -> absolute
                            from urllib.parse import urljoin

                            img_url = urljoin(url, src)
                            try:
                                ir = self._page_sess.get(img_url, timeout=6)
                                if ir.status_code != 200:
                                    continue
                                im = _open_image_safe(ir.content)
                                images.append(im)
                            except Exception:
                                continue
                    ocr_texts = []
                    for im in images[:3]:
                        try:
                            ocr_texts.append(pytesseract.image_to_string(im, lang='ita'))
                        except Exception:
                            continue
                    if ocr_texts:
                        page_text = page_text + " " + " ".join(ocr_texts)
                except Exception:
                    pass
                return page_text
            except Exception:
                time.sleep(backoff)
                backoff *= 2
                if attempt == 2:
                    return ""

    def search_polizia_locale(
        self,
        comune: str,
        provincia: str = "",
        domain_hint: str = "",
        deep: bool = True,
        max_total_seconds: float = 45.0,
        extra_queries: list[str] | None = None,
        strict_pl_local: bool = True,
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
        start_time = time.monotonic()

        # combina eventuali query extra fornite dall'utente in testa
        # formatta eventuali query extra sostituendo i placeholder
        formatted_extras: list[str] = []
        if extra_queries:
            for q in extra_queries:
                if not q:
                    continue
                fq = q.replace("{comune}", comune).replace("{provincia}", provincia).replace("{prov}", provincia)
                formatted_extras.append(fq)

        queries = formatted_extras + _build_queries(comune, provincia)
        for query in queries:
            if time.monotonic() - start_time > max_total_seconds:
                break
            results = self._search(query, count=12)
            if not results:
                continue

            # 1) Cerca mail PL-specifiche direttamente negli snippet
            snippet_text = " ".join(
                [(r.get("description") or "") + " " + (r.get("title") or "") for r in results]
            )
            pec, mail = _harvest_emails(
                snippet_text, _domain_ok, only_pl_specific=strict_pl_local
            )
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
                    if time.monotonic() - start_time > max_total_seconds:
                        break
                    text = self._fetch_page(url)
                    if not text:
                        continue
                    pec, mail = _harvest_emails(
                        text, _domain_ok, only_pl_specific=strict_pl_local
                    )
                    if pec or mail:
                        pec_all |= pec
                        mail_all |= mail
                        sources.append(url)
                        break
            if pec_all or mail_all:
                break

        # se non trovate mail e abilitato debug, dump delle query/snippet per diagnostica
        try:
            if (not pec_all and not mail_all) and os.environ.get("PL_DEBUG_WEB") == "1":
                dump = {
                    "comune": comune,
                    "provincia": provincia,
                    "tried_queries": [],
                    "sources_sample": sources,
                }
                for query in _build_queries(comune, provincia):
                    res = self._search(query, count=4)
                    snippets = " ".join([(r.get("description") or "") + " " + (r.get("title") or "") for r in res])
                    dump["tried_queries"].append({"query": query, "snippet": snippets})
                os.makedirs("logs/web_debug", exist_ok=True)
                fname = f"logs/web_debug/{comune.replace(' ', '_')}_{int(time.time())}.json"
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(dump, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return pec_all, mail_all, sources

    def close(self) -> None:
        with suppress(Exception):
            self._sess.close()
            self._page_sess.close()
