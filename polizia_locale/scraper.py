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


def _candidate_links(soup: BeautifulSoup, base: str) -> list[tuple[str, int]]:
    """Estrae i link interni candidati con un punteggio di priorità.

    Score più alto = più specifico per la Polizia Locale.
      3 → href/testo contiene "polizia local/municipal", "vigili urbani",
          "comando pl/pm", "comando polizia"
      2 → href/testo contiene "polizia", "vigili", "comando"
      1 → href/testo contiene "uffici", "amministrazione", "contatti"
    """
    scored: list[tuple[str, int]] = []
    base_host = urlparse(base).netloc
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        href = a["href"]
        hay = (text + " " + href.lower())
        score = 0
        if any(
            k in hay
            for k in [
                "polizia local",
                "polizia municipal",
                "polizia-local",
                "polizia-municipal",
                "polizia_local",
                "polizia_municipal",
                "vigili urbani",
                "vigili-urbani",
                "comando p.m",
                "comando pl",
                "comando pm",
                "comando polizia",
                "comando-polizia",
            ]
        ):
            score = 3
        elif any(k in hay for k in ["polizia", "vigili", "comando"]):
            score = 2
        elif any(k in hay for k in ["uffici", "amministrazione/uffici", "amministrazione", "punto_contatto", "contatti"]):
            score = 1
        if score == 0:
            continue
        absu = urljoin(base, href)
        if not absu.startswith("http"):
            continue
        # solo link interni allo stesso dominio
        if urlparse(absu).netloc and urlparse(absu).netloc != base_host:
            continue
        # niente media/asset
        if any(absu.lower().endswith(ext) for ext in (".jpg", ".png", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")):
            continue
        scored.append((absu, score))
    # ordina per score desc, mantieni ordine di apparizione a parità di score
    seen = set()
    out: list[tuple[str, int]] = []
    for url, score in sorted(scored, key=lambda x: -x[1]):
        if url in seen:
            continue
        seen.add(url)
        out.append((url, score))
    return out


def _extract_emails_with_context(html: str) -> list[tuple[str, str]]:
    """Ritorna (email, contesto_breve)."""
    out = []
    for m in EMAIL_RE.finditer(html):
        start = max(0, m.start() - 80)
        end = min(len(html), m.end() + 80)
        ctx = html[start:end]
        out.append((m.group(0), ctx))
    return out


def _filter_polizia_emails(
    pairs: list[tuple[str, str]],
    page_is_polizia: bool = False,
) -> tuple[set[str], set[str]]:
    """Restituisce (pec_set, email_set) limitate al contesto polizia locale.

    Se `page_is_polizia` è True, accettiamo tutte le mail trovate sulla pagina
    (perché l'URL/titolo indica chiaramente che la pagina è dedicata alla PL).
    """
    pec: set[str] = set()
    mail: set[str] = set()
    for email, ctx in pairs:
        local = email.split("@")[0].lower()
        domain = email.split("@", 1)[1].lower() if "@" in email else ""
        ctx_l = ctx.lower()
        is_pl_local = any(
            k in local
            for k in [
                "polizialocale",
                "poliziamunicipale",
                "vigili",
                "comandopm",
                "comandopl",
                "pl.",
                "pm.",
                "pol.locale",
                "pol.municipale",
            ]
        )
        is_pl_ctx = any(
            k in ctx_l
            for k in [
                "polizia local",
                "polizia municipal",
                "vigili urbani",
                "comando p.m",
                "comando pl",
                "comando pm",
                "comando di polizia",
                "polizia urbana",
            ]
        )
        if not (page_is_polizia or is_pl_local or is_pl_ctx):
            continue
        # escludi noreply, info generiche solo se il contesto non è chiaro
        if local in {"noreply", "no-reply", "webmaster", "postmaster"}:
            continue
        # escludi loghi/immagini con estensioni
        if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
            continue
        # escludi pattern ovvi di domini fake
        if "example.com" in domain or "test.it" in domain:
            continue
        if _is_pec(email, ctx):
            pec.add(email)
        else:
            mail.add(email)
    return pec, mail


# URL pattern comuni dei comandi/uffici di Polizia Locale sui siti istituzionali.
# Includiamo varianti italiane e con prefisso /it (CMS Drupal/Plone della PA).
_DIRECT_PATH_HINTS = (
    "/polizia-locale",
    "/polizia-municipale",
    "/poliziamunicipale",
    "/polizialocale",
    "/comando-polizia-locale",
    "/comando-polizia-municipale",
    "/vigili-urbani",
    "/aree/polizia-locale",
    "/aree/polizia-municipale",
    "/aree/comando-polizia-municipale",
    "/uffici/polizia-locale",
    "/uffici/polizia-municipale",
    "/uffici/comando-polizia-municipale",
    "/uffici/comando-polizia-locale",
    "/servizi/polizia-locale",
    "/servizi/polizia-municipale",
    "/amministrazione/polizia-locale",
    "/amministrazione/polizia-municipale",
    "/amministrazione/uffici/polizia-locale",
    "/amministrazione/uffici/polizia-municipale",
    "/amministrazione/uffici/comando-polizia-municipale",
    "/amministrazione/uffici/comando-polizia-locale",
    "/it/polizia-locale",
    "/it/polizia-municipale",
    "/it/comando-polizia-municipale",
    "/it/uffici/polizia-locale",
    "/it/uffici/polizia-municipale",
    "/it/amministrazione/uffici/polizia-locale",
    "/it/amministrazione/uffici/polizia-municipale",
    "/it/amministrazione/uffici/comando-polizia-municipale",
    "/it/amministrazione/uffici/comando-polizia-locale",
    "/it/aree/polizia-municipale",
    "/it/aree/polizia-locale",
    "/it/servizi/polizia-locale",
    "/it/servizi/polizia-municipale",
    "/punto_contatto/polizia-municipale",
    "/punto_contatto/polizia-locale",
    "/it/punto_contatto/polizia-municipale",
    "/it/punto_contatto/polizia-locale",
)


def _path_is_polizia(url: str) -> bool:
    u = url.lower()
    return any(
        kw in u
        for kw in (
            "polizia-local",
            "polizia-municipal",
            "polizia_local",
            "polizia_municipal",
            "poliziamunicipale",
            "polizialocale",
            "vigili-urban",
            "comando-polizia",
            "comando-pl",
            "comando-pm",
        )
    )


def scrape_polizia_locale(
    comune: str,
    provincia: str,
    codice_istat: str,
    site_hint: str = "",
    timeout: int = 15,
    total_budget: float = 40.0,
    max_candidates: int = 4,
    strict_pl_local: bool = True,
) -> ScrapeResult | None:
    """Cerca PEC/email della Polizia Locale sul sito comunale.

    Se `strict_pl_local=True` (default), accetta SOLO mail con local-part
    PL-specifica (polizialocale@, vigili@, comandopm@, …). Niente PEC/mail
    generiche del Comune.

    Strategia di ricerca (in ordine):
      1. Visita i path noti come /polizia-locale, /polizia-municipale, …
      2. Prova sottodomini dedicati: pm.X, pl.X, polizia.X, polizialocale.X,
         poliziamunicipale.X, vigili.X.
      3. Apre la homepage e segue i candidate links.
    Si ferma appena trova mail PL-specifiche.
    """
    from .indicepa import is_pl_specific_email

    deadline = time.monotonic() + total_budget
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
    host = parsed.netloc.lstrip("www.")

    pages_visited: list[str] = []
    pec_all: set[str] = set()
    mail_all: set[str] = set()

    def _harvest(html: str, url_is_pl: bool):
        if strict_pl_local:
            for m in EMAIL_RE.finditer(html):
                e = m.group(0)
                if is_pl_specific_email(e):
                    if _is_pec(e, html[max(0, m.start()-80):m.end()+80]):
                        pec_all.add(e)
                    else:
                        mail_all.add(e)
        else:
            pairs = _extract_emails_with_context(html)
            p, ml = _filter_polizia_emails(pairs, page_is_polizia=url_is_pl)
            pec_all.update(p)
            mail_all.update(ml)

    try:
        # 1) Path diretti
        for path in _DIRECT_PATH_HINTS:
            if time.monotonic() > deadline or (pec_all or mail_all):
                break
            url = base + path
            try:
                rr = session.get(url, timeout=req_timeout, allow_redirects=True)
                if rr.status_code != 200:
                    continue
                pages_visited.append(rr.url)
                _harvest(rr.text, url_is_pl=_path_is_polizia(rr.url))
            except Exception:
                continue

        # 2) Sottodomini dedicati (pm.X, pl.X, polizia.X)
        if not (pec_all or mail_all):
            # timeout aggressivo per sottodomini (spesso DNS non risponde)
            sub_timeout = (3, 5)
            for sub in ("pm", "pl", "polizia"):
                if time.monotonic() > deadline or (pec_all or mail_all):
                    break
                sub_url = f"{parsed.scheme}://{sub}.{host}/"
                try:
                    rr = session.get(sub_url, timeout=sub_timeout, allow_redirects=True)
                    if rr.status_code != 200:
                        continue
                    pages_visited.append(rr.url)
                    _harvest(rr.text, url_is_pl=True)
                    # esplora qualche link interno
                    if not (pec_all or mail_all):
                        soup = BeautifulSoup(rr.text, "html.parser")
                        for a in soup.find_all("a", href=True)[:20]:
                            t = (a.get_text() or "").lower()
                            if any(k in t or k in a["href"].lower() for k in ["contatt", "sedi"]):
                                u = urljoin(rr.url, a["href"])
                                if u.startswith("http"):
                                    try:
                                        rr2 = session.get(u, timeout=sub_timeout)
                                        pages_visited.append(rr2.url)
                                        _harvest(rr2.text, url_is_pl=True)
                                        if pec_all or mail_all:
                                            break
                                    except Exception:
                                        continue
                except Exception:
                    continue

        # 3) Homepage + candidate links con BFS 2 livelli
        if not (pec_all or mail_all):
            try:
                r = session.get(site, timeout=req_timeout)
                pages_visited.append(r.url)
                soup = BeautifulSoup(r.text, "html.parser")
                _harvest(r.text, url_is_pl=False)

                # Livello 1: visita i link più specifici prima
                candidates = _candidate_links(soup, base)
                visited_level1: set[str] = set()
                # priorità: prima score=3, poi 2, poi 1 (incluso uffici/amm)
                lvl1_high = [u for u, s in candidates if s == 3][:6]
                lvl1_med = [u for u, s in candidates if s == 2][:4]
                lvl1_low = [u for u, s in candidates if s == 1][:6]
                queue_lvl1 = lvl1_high + lvl1_med + lvl1_low

                for url in queue_lvl1:
                    if time.monotonic() > deadline or (pec_all or mail_all):
                        break
                    if url in visited_level1:
                        continue
                    visited_level1.add(url)
                    try:
                        rr = session.get(url, timeout=req_timeout, allow_redirects=True)
                        if rr.status_code != 200:
                            continue
                        pages_visited.append(rr.url)
                        is_pl = _path_is_polizia(rr.url) or _path_is_polizia(url)
                        _harvest(rr.text, url_is_pl=is_pl)
                        if pec_all or mail_all:
                            break
                        # Livello 2: se la pagina è un indice "uffici" o
                        # "amministrazione", scendi cercando link a PL
                        sub_soup = BeautifulSoup(rr.text, "html.parser")
                        lvl2 = _candidate_links(sub_soup, base)
                        # ora accettiamo score=3 e score=2 (PL/polizia/vigili/comando)
                        lvl2_targets = [
                            u for u, s in lvl2 if s >= 2 and u not in visited_level1
                        ][:6]
                        for u2 in lvl2_targets:
                            if time.monotonic() > deadline or (pec_all or mail_all):
                                break
                            visited_level1.add(u2)
                            try:
                                rr2 = session.get(u2, timeout=req_timeout, allow_redirects=True)
                                if rr2.status_code != 200:
                                    continue
                                pages_visited.append(rr2.url)
                                is_pl2 = _path_is_polizia(rr2.url) or _path_is_polizia(u2)
                                _harvest(rr2.text, url_is_pl=is_pl2)
                                if pec_all or mail_all:
                                    break
                                # Livello 3 (solo se la pagina visitata è un indice
                                # PL "comando/polizia/vigili" → cerca pagine
                                # dettaglio interne)
                                if is_pl2:
                                    sub2 = BeautifulSoup(rr2.text, "html.parser")
                                    lvl3 = _candidate_links(sub2, base)
                                    for u3, s3 in lvl3:
                                        if s3 < 2 or u3 in visited_level1:
                                            continue
                                        if time.monotonic() > deadline or (pec_all or mail_all):
                                            break
                                        visited_level1.add(u3)
                                        try:
                                            rr3 = session.get(u3, timeout=req_timeout, allow_redirects=True)
                                            if rr3.status_code != 200:
                                                continue
                                            pages_visited.append(rr3.url)
                                            _harvest(rr3.text, url_is_pl=True)
                                            if pec_all or mail_all:
                                                break
                                        except Exception:
                                            continue
                            except Exception:
                                continue
                    except Exception:
                        continue
            except Exception:
                pass
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
