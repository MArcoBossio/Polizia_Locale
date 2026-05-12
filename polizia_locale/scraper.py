"""Fallback: scraping del sito istituzionale del comune alla ricerca di mail/PEC
specifiche della Polizia Locale/Municipale.

Strategia:
  1. Per ogni comune senza match in IndicePA, parte dal sito istituzionale
     (se noto via dataset Enti) oppure interroga DuckDuckGo HTML.
  2. Carica fino a N pagine candidate (homepage, sitemap, contatti, pagine
     che nella URL/titolo contengono "polizia").
  3. Estrae email/PEC con regex, classificando come PEC quelle che contengono
     domini tipici (pec.*, *.legalmail.it, *.postacertificata.gov.it) o la
     parola "pec".
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .utils import USER_AGENT

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

PEC_DOMAIN_HINTS = (
    "pec.",
    ".pec.",
    "legalmail",
    "postecert",
    "pec-",
    "-pec",
    "postacertificata",
    "actaliscertymail",
    "cert.",
)


def _is_pec(email: str, context: str = "") -> bool:
    e = email.lower()
    if any(h in e for h in PEC_DOMAIN_HINTS):
        return True
    if "pec" in context.lower():
        return True
    return False


@dataclass
class ScrapeResult:
    comune: str
    codice_istat: str
    pec: str
    email: str
    sito: str
    pagina: str
    fonte: str = "ScrapingSitoComune"

    def as_dict(self) -> dict:
        return {
            "comune": self.comune,
            "codice_istat": self.codice_istat,
            "codice_ipa": "",
            "denominazione_ente": f"Comune di {self.comune}",
            "codice_uni_uo": "",
            "descrizione_uo": "Polizia Locale (da sito comunale)",
            "pec": self.pec,
            "email": self.email,
            "telefono": "",
            "indirizzo": "",
            "cap": "",
            "fonte": self.fonte,
            "sito": self.sito,
            "pagina": self.pagina,
        }


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        }
    )
    return s


def duckduckgo_first_result(session: requests.Session, query: str) -> str:
    """Restituisce la prima URL utile da DuckDuckGo HTML."""
    try:
        r = session.post(
            "https://duckduckgo.com/html/",
            data={"q": query},
            timeout=(5, 10),
        )
        r.raise_for_status()
    except Exception:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("a.result__a, a.result__url"):
        href = a.get("href", "")
        if not href:
            continue
        # i link DDG sono spesso /l/?uddg=<encoded>
        from urllib.parse import parse_qs, urlparse as up

        u = up(href)
        if u.path == "/l/":
            qs = parse_qs(u.query)
            target = qs.get("uddg", [""])[0]
            if target:
                return target
        if href.startswith("http"):
            return href
    return ""


def find_comune_website(session: requests.Session, comune: str, provincia: str) -> str:
    q = f'sito ufficiale "Comune di {comune}" {provincia} polizia locale'
    url = duckduckgo_first_result(session, q)
    if not url:
        url = duckduckgo_first_result(session, f'sito istituzionale comune di {comune} {provincia}')
    return url


def _candidate_links(soup: BeautifulSoup, base: str) -> list[str]:
    links = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        href = a["href"]
        if any(k in (text + " " + href.lower()) for k in [
            "polizia local", "polizia municipal", "vigili urbani",
            "comando p.m", "comando pl", "comando pm",
            "contatti", "amministrazione", "uffici",
        ]):
            absu = urljoin(base, href)
            if absu.startswith("http"):
                links.append(absu)
    # rimuovi duplicati mantenendo ordine
    seen = set()
    out = []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:8]


def _extract_emails_with_context(html: str) -> list[tuple[str, str]]:
    """Ritorna (email, contesto_breve)."""
    out = []
    for m in EMAIL_RE.finditer(html):
        start = max(0, m.start() - 80)
        end = min(len(html), m.end() + 80)
        ctx = html[start:end]
        out.append((m.group(0), ctx))
    return out


def _filter_polizia_emails(pairs: list[tuple[str, str]]) -> tuple[set[str], set[str]]:
    """Restituisce (pec_set, email_set) limitate al contesto polizia locale."""
    pec: set[str] = set()
    mail: set[str] = set()
    for email, ctx in pairs:
        local = email.split("@")[0].lower()
        domain = email.split("@", 1)[1].lower() if "@" in email else ""
        ctx_l = ctx.lower()
        is_pl_local = any(k in local for k in ["polizialocale", "poliziamunicipale", "vigili", "comandopm", "comandopl", "pl.", "pm."])
        is_pl_ctx = any(k in ctx_l for k in ["polizia local", "polizia municipal", "vigili urbani", "comando p.m", "comando pl", "comando pm"])
        if not (is_pl_local or is_pl_ctx):
            continue
        # escludi noreply, info generiche solo se il contesto non è chiaro
        if local in {"noreply", "no-reply"}:
            continue
        # escludi domini non istituzionali ovvi
        if domain.endswith(("@example.com", ".png", ".jpg")):
            continue
        if _is_pec(email, ctx):
            pec.add(email)
        else:
            mail.add(email)
    return pec, mail


def scrape_polizia_locale(
    comune: str,
    provincia: str,
    codice_istat: str,
    site_hint: str = "",
    timeout: int = 15,
    total_budget: float = 25.0,
    max_candidates: int = 4,
) -> ScrapeResult | None:
    """Cerca PEC/email della Polizia Locale sul sito comunale.

    `timeout` è il timeout (connect, read) di ogni singola request; `total_budget`
    è il tempo massimo totale dedicato a questo comune (tutte le pagine).
    """
    deadline = time.monotonic() + total_budget
    # split timeout: connect più aggressivo del read
    req_timeout = (min(timeout, 8), timeout)
    session = _new_session()
    site = site_hint.strip() or find_comune_website(session, comune, provincia)
    if not site or time.monotonic() > deadline:
        return None
    parsed = urlparse(site)
    if not parsed.scheme:
        site = "https://" + site
        parsed = urlparse(site)
    base = f"{parsed.scheme}://{parsed.netloc}"

    pages_visited: list[str] = []
    pec_all: set[str] = set()
    mail_all: set[str] = set()
    try:
        r = session.get(site, timeout=req_timeout)
        pages_visited.append(r.url)
        soup = BeautifulSoup(r.text, "html.parser")
        pec, mail = _filter_polizia_emails(_extract_emails_with_context(r.text))
        pec_all |= pec
        mail_all |= mail

        candidates = _candidate_links(soup, base)[:max_candidates]
        for url in candidates:
            if time.monotonic() > deadline:
                break
            try:
                rr = session.get(url, timeout=req_timeout)
                pages_visited.append(rr.url)
                pec, mail = _filter_polizia_emails(_extract_emails_with_context(rr.text))
                pec_all |= pec
                mail_all |= mail
                if pec_all or mail_all:
                    break
            except Exception:
                continue
    except Exception:
        if not (pec_all or mail_all):
            return None

    if not pec_all and not mail_all:
        return None
    return ScrapeResult(
        comune=comune,
        codice_istat=codice_istat,
        pec=" | ".join(sorted(pec_all)),
        email=" | ".join(sorted(mail_all)),
        sito=base,
        pagina=pages_visited[-1] if pages_visited else "",
    )
