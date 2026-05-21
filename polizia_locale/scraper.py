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

import asyncio
import re
import time
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urldefrag, urljoin, urlparse
import unicodedata

import requests
from bs4 import BeautifulSoup

from .normalization import similarity
from .utils import USER_AGENT
from .utils import is_likely_personal_email

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

PL_HINTS = (
    "polizia locale",
    "polizia municipale",
    "polizia urbana",
    "vigili urbani",
    "comando",
    "corpo di polizia",
    "corpo unico",
    "servizio associato di polizia",
    "servizio associato di vigilanza",
    "ufficio vigilanza",
    "police locale",
    "service de police",
    "service de police locale",
    "union des communes",
    "municipalite",
    "municipalité",
    "vigiles",
    "ortspolizei",
    "gemeindepolizei",
    "polizeiamt",
)

PL_CONTEXT_CONCEPTS = (
    "polizia locale",
    "polizia municipale",
    "vigili urbani",
    "comando polizia locale",
    "servizio associato di polizia locale",
    "servizio associato di vigilanza",
    "ufficio vigilanza",
    "corpo unico",
    "police locale",
    "service de police locale",
    "service de police",
    "union des communes",
    "municipalité",
    "municipalite",
    "vigiles",
    "ortspolizei",
    "gemeindepolizei",
    "polizeiamt",
)

NEGATIVE_HINTS = (
    "ragioneria",
    "tributi",
    "anagrafe",
    "urp",
    "protocollo",
    "segreteria",
    "personale",
    "finanzi",
)


def _normalize_match_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _context_segments(text: str) -> list[str]:
    return [segment.strip() for segment in re.split(r"[|;/\n]+", _normalize_match_text(text)) if segment.strip()]


def _best_context_similarity(text: str, concepts: tuple[str, ...]) -> float:
    segments = _context_segments(text)
    if not segments:
        return 0.0
    best = 0.0
    for segment in segments:
        for concept in concepts:
            best = max(best, similarity(segment, concept))
    return best


def _append_unique(parts: list[str], text: str) -> None:
    cleaned = _normalize_match_text(text)
    if cleaned and cleaned not in parts:
        parts.append(cleaned)


def _dom_context_for_node(node, soup: BeautifulSoup, email: str) -> str:
    parts: list[str] = []
    try:
        if hasattr(node, "get_text"):
            source_text = node.get_text(" ", strip=True)
        else:
            source_text = str(node)
    except Exception:
        source_text = str(node)
    _append_unique(parts, source_text)

    # Se il node è dentro un <a href="mailto:...">, raccogli il testo dell'anchor
    parent = getattr(node, "parent", None)
    if parent and getattr(parent, "name", None) == "a":
        _append_unique(parts, parent.get_text(" ", strip=True))
        for attr in ("aria-label", "title", "data-label"):
            value = parent.get(attr)
            if value:
                _append_unique(parts, str(value))

    # Raccogli sibling precedente e successivo
    if parent:
        try:
            prev_sibling = parent.find_previous_sibling()
            if prev_sibling:
                _append_unique(parts, prev_sibling.get_text(" ", strip=True))
                for heading in prev_sibling.find_all(["h1", "h2", "h3", "h4", "h5", "h6"], limit=2):
                    _append_unique(parts, heading.get_text(" ", strip=True))
        except Exception:
            pass
        try:
            next_sibling = parent.find_next_sibling()
            if next_sibling:
                _append_unique(parts, next_sibling.get_text(" ", strip=True))
        except Exception:
            pass

    # Risali gli ancestor
    current = parent
    ancestor_count = 0
    while current is not None and ancestor_count < 5:
        if getattr(current, "name", None):
            _append_unique(parts, current.get_text(" ", strip=True))
            for attr in ("aria-label", "title", "data-label", "data-title"):
                value = current.get(attr)
                if value:
                    _append_unique(parts, str(value))
            # Heading precedente nel contesto dell'ancestor
            try:
                headings = current.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"], limit=3)
            except Exception:
                headings = []
            for heading in headings:
                _append_unique(parts, heading.get_text(" ", strip=True))
            
            # Per section/article/div[@class*="contact"], raccogli il titolo
            if getattr(current, "name", None) in ("section", "article"):
                for heading in current.find_all(["h1", "h2", "h3", "h4", "h5", "h6"], limit=2):
                    _append_unique(parts, heading.get_text(" ", strip=True))
        current = getattr(current, "parent", None)
        ancestor_count += 1

    title = getattr(getattr(soup, "title", None), "string", "") or ""
    _append_unique(parts, title)

    for breadcrumb in soup.select(
        ".breadcrumb, nav[aria-label*='breadcrumb' i], [aria-label*='breadcrumb' i]"
    )[:3]:
        _append_unique(parts, breadcrumb.get_text(" ", strip=True))

    if email:
        _append_unique(parts, email)

    return " | ".join(parts)


def _is_pec(email: str, context: str = "") -> bool:
    e = email.lower()
    if any(h in e for h in PEC_DOMAIN_HINTS):
        return True
    if "pec" in context.lower():
        return True
    return False


def _is_strict_pl_local_email(email: str) -> bool:
    if not is_likely_personal_email(email) and "@" in email:
        local = email.split("@", 1)[0].lower().strip()
        if local in {"info", "segreteria"}:
            return False
    return False if not email else any(
        k in email.split("@", 1)[0].lower().strip()
        for k in (
            "polizialocale",
            "poliziamunicipale",
            "vigili",
            "comandopm",
            "comandopl",
            "pol.locale",
            "pol.municipale",
            "pm.",
            "pl.",
        )
    )


@dataclass
class ScrapeResult:
    comune: str
    codice_istat: str
    pec: str
    email: str
    sito: str
    pagina: str
    confidence: float = 0.0
    matched_by: str = ""
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
            "confidence": round(self.confidence, 3),
            "matched_by": self.matched_by,
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


async def _aiohttp_fetch_many(
    urls: list[str],
    timeout: tuple[int, int],
    headers: dict[str, str],
) -> list[tuple[str, int, str, str]]:
    try:
        aiohttp = __import__("aiohttp")
    except Exception:
        return []

    if not urls:
        return []

    client_timeout = aiohttp.ClientTimeout(
        total=max(timeout[1] * 2, timeout[0] + timeout[1] + 2),
        connect=timeout[0],
        sock_connect=timeout[0],
        sock_read=timeout[1],
    )
    connector = aiohttp.TCPConnector(limit=min(8, max(1, len(urls))))

    async def _fetch_one(session, url: str) -> tuple[str, int, str, str]:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                text = await resp.text(errors="ignore")
                return url, resp.status, str(resp.url), text
        except Exception:
            return url, 0, url, ""

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=client_timeout,
        connector=connector,
    ) as session:
        return await asyncio.gather(*(_fetch_one(session, url) for url in urls))


def _fetch_many(
    session: requests.Session,
    urls: list[str],
    timeout: tuple[int, int],
) -> list[tuple[str, int, str, str]]:
    if not urls:
        return []
    if session.__class__.__module__ != requests.Session.__module__:
        out: list[tuple[str, int, str, str]] = []
        for url in urls:
            try:
                rr = session.get(url, timeout=timeout, allow_redirects=True)
                out.append((url, rr.status_code, rr.url, rr.text))
            except Exception:
                out.append((url, 0, url, ""))
        return out
    headers = dict(session.headers)
    try:
        return asyncio.run(_aiohttp_fetch_many(urls, timeout, headers))
    except Exception:
        out: list[tuple[str, int, str, str]] = []
        for url in urls:
            try:
                rr = session.get(url, timeout=timeout, allow_redirects=True)
                out.append((url, rr.status_code, rr.url, rr.text))
            except Exception:
                out.append((url, 0, url, ""))
        return out


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


def _candidate_links(soup: BeautifulSoup, base: str, page_is_pl: bool = False) -> list[tuple[str, int]]:
    """Estrae i link interni candidati con un punteggio di priorità.

    Score più alto = più specifico per la Polizia Locale.
      3 → href/testo contiene "polizia local/municipal", "vigili urbani",
          "comando pl/pm", "comando polizia"
      2 → href/testo contiene "polizia", "vigili", "comando"
      1 → href/testo contiene "uffici", "amministrazione", "contatti"
    """
    scored: list[tuple[str, int]] = []
    base_host = urlparse(base).netloc
    base_clean = urldefrag(base).url
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        href = a["href"]
        if not href or href.startswith("#"):
            continue
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
        if score == 0 and page_is_pl:
            if any(k in hay for k in ["node/", "/uffici", "/servizi", "/amministrazione", "/contatti"]):
                score = 1
            elif text:
                score = 1
        if score == 0:
            continue
        absu = urljoin(base, href)
        if not absu.startswith("http"):
            continue
        if urldefrag(absu).url == base_clean:
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


def _broad_candidate_links(soup: BeautifulSoup, base: str, limit: int = 8, page_is_pl: bool = False) -> list[str]:
    """Fallback più largo per siti con struttura molto diversa.

    Quando i path espliciti della PL non esistono, proviamo anche link meno
    specifici ma ancora plausibili (contatti, uffici, servizi, segreteria,
    supporto), mantenendo solo quelli interni allo stesso dominio.
    """
    base_host = urlparse(base).netloc
    base_clean = urldefrag(base).url
    scored: list[tuple[str, int]] = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        href = a["href"]
        if not href or href.startswith("#"):
            continue
        hay = f"{text} {href.lower()}"
        score = 0
        if any(k in hay for k in ("polizia", "vigili", "comando", "municipale")):
            score = 4
        elif any(k in hay for k in ("contatt", "email", "pec", "urp", "sportello", "assistenza", "support", "help")):
            score = 3
        elif any(k in hay for k in ("uffici", "servizi", "amministrazione", "segreteria", "anagrafe", "protocollo")):
            score = 2
        elif any(k in hay for k in ("news", "avvisi", "trasparenza", "redazione", "faq")):
            score = 1
        if score == 0 and page_is_pl:
            score = 1 if text or href else 0
        if score == 0:
            continue
        absu = urljoin(base, href)
        if not absu.startswith("http"):
            continue
        if urldefrag(absu).url == base_clean:
            continue
        if urlparse(absu).netloc and urlparse(absu).netloc != base_host:
            continue
        if any(absu.lower().endswith(ext) for ext in (".jpg", ".png", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")):
            continue
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


def _extract_emails_with_context(html: str) -> list[tuple[str, str]]:
    """Ritorna (email, contesto_breve)."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    soup = BeautifulSoup(html, "html.parser")

    for node in soup.find_all(string=True):
        text = str(node)
        if "@" not in text:
            continue
        for m in EMAIL_RE.finditer(text):
            email = m.group(0)
            ctx = _dom_context_for_node(node, soup, email)
            key = (email, ctx)
            if key in seen:
                continue
            seen.add(key)
            out.append((email, ctx))

    for tag in soup.find_all(href=True):
        href = tag.get("href", "")
        if not href.lower().startswith("mailto:"):
            continue
        raw = href.split(":", 1)[1].split("?", 1)[0]
        for m in EMAIL_RE.finditer(raw):
            email = m.group(0)
            ctx = _dom_context_for_node(tag, soup, email)
            key = (email, ctx)
            if key in seen:
                continue
            seen.add(key)
            out.append((email, ctx))

    if out:
        return out

    for m in EMAIL_RE.finditer(html):
        start = max(0, m.start() - 120)
        end = min(len(html), m.end() + 120)
        out.append((m.group(0), html[start:end]))
    return out


def _score_email_context(html: str, email: str, ctx: str = "") -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    norm_ctx = _normalize_match_text(ctx)
    if any(h in norm_ctx for h in PL_HINTS):
        score += 2
        reasons.append("context_polizia")
    elif _best_context_similarity(norm_ctx, PL_CONTEXT_CONCEPTS) >= 0.78:
        score += 2
        reasons.append("context_fuzzy_polizia")
    if any(h in norm_ctx for h in NEGATIVE_HINTS):
        score -= 2
        reasons.append("context_non_pl")
    local = email.split("@", 1)[0].lower()
    if any(k in local for k in ("polizialocale", "poliziamunicipale", "vigili", "comandopm", "comandopl", "pol.locale", "pol.municipale", "info", "segreteria")):
        score += 3
        reasons.append("local_part_pl")
    if _is_pec(email, ctx):
        score += 1
        reasons.append("pec_hint")

    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Process text nodes containing email
        nodes = soup.find_all(string=lambda s: isinstance(s, str) and email.lower() in s.lower())
        for node in nodes[:3]:
            parent = getattr(node, "parent", None)
            if not parent:
                continue
            parent_text = _normalize_match_text(parent.get_text(" ", strip=True))
            if any(h in parent_text for h in PL_HINTS):
                score += 2
                reasons.append("dom_parent_pl")
            
            # Controllare sibling precedente (spesso heading)
            try:
                prev_sibling = parent.find_previous_sibling()
                if prev_sibling:
                    sibling_text = _normalize_match_text(prev_sibling.get_text(" ", strip=True))
                    for heading_tag in prev_sibling.find_all(["h1", "h2", "h3", "h4", "h5", "h6"], limit=2):
                        heading_text = _normalize_match_text(heading_tag.get_text(" ", strip=True))
                        if any(h in heading_text for h in PL_HINTS):
                            score += 2
                            reasons.append("sibling_context_pl")
                            break
                        elif _best_context_similarity(heading_text, PL_CONTEXT_CONCEPTS) >= 0.75:
                            score += 1
                            reasons.append("sibling_context_pl")
                            break
            except Exception:
                pass
            
            # Controllare section/article parent per titolo
            section = parent
            for _ in range(3):
                section = getattr(section, "parent", None)
                if not section:
                    break
                if getattr(section, "name", None) in ("section", "article"):
                    for heading in section.find_all(["h1", "h2", "h3", "h4", "h5", "h6"], limit=2):
                        heading_text = _normalize_match_text(heading.get_text(" ", strip=True))
                        if any(h in heading_text for h in PL_HINTS):
                            score += 1
                            reasons.append("section_context_pl")
                            break
                        elif _best_context_similarity(heading_text, PL_CONTEXT_CONCEPTS) >= 0.75:
                            score += 1
                            reasons.append("section_context_pl")
                            break
            
            prev_headings = parent.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "label"], limit=4)
            heading_text = _normalize_match_text(" ".join(tag.get_text(" ", strip=True) for tag in prev_headings))
            if any(h in heading_text for h in PL_HINTS):
                score += 1
                reasons.append("dom_heading_pl")
                break
        
        # Process anchor tags with mailto href
        for tag in soup.find_all(href=True):
            href = tag.get("href", "").lower()
            if not href.startswith("mailto:"):
                continue
            raw = tag.get("href", "").split(":", 1)[1].split("?", 1)[0].lower()
            if email.lower() not in raw:
                continue
            
            # Se parent è un anchor, controllarne il testo
            anchor_text = tag.get_text(" ", strip=True).lower()
            if anchor_text and any(k in anchor_text for k in PL_HINTS):
                score += 1
                reasons.append("anchor_context_pl")
            elif anchor_text and _best_context_similarity(anchor_text, PL_CONTEXT_CONCEPTS) >= 0.75:
                score += 1
                reasons.append("anchor_context_pl")
            
            # Controllare sibling precedente del parent (spesso heading)
            try:
                parent = getattr(tag, "parent", None)
                if parent:
                    prev_sibling = parent.find_previous_sibling()
                    if prev_sibling:
                        # Se prev_sibling è un heading direttamente
                        if getattr(prev_sibling, "name", None) in ("h1", "h2", "h3", "h4", "h5", "h6"):
                            heading_text = _normalize_match_text(prev_sibling.get_text(" ", strip=True))
                            if any(h in heading_text for h in PL_HINTS):
                                score += 2
                                if "sibling_context_pl" not in reasons:
                                    reasons.append("sibling_context_pl")
                            elif _best_context_similarity(heading_text, PL_CONTEXT_CONCEPTS) >= 0.75:
                                score += 1
                                if "sibling_context_pl" not in reasons:
                                    reasons.append("sibling_context_pl")
                        # Altrimenti cerca heading inside prev_sibling
                        else:
                            sibling_text = _normalize_match_text(prev_sibling.get_text(" ", strip=True))
                            for heading_tag in prev_sibling.find_all(["h1", "h2", "h3", "h4", "h5", "h6"], limit=2):
                                heading_text = _normalize_match_text(heading_tag.get_text(" ", strip=True))
                                if any(h in heading_text for h in PL_HINTS):
                                    score += 2
                                    if "sibling_context_pl" not in reasons:
                                        reasons.append("sibling_context_pl")
                                    break
                                elif _best_context_similarity(heading_text, PL_CONTEXT_CONCEPTS) >= 0.75:
                                    score += 1
                                    if "sibling_context_pl" not in reasons:
                                        reasons.append("sibling_context_pl")
                                    break
            except Exception:
                pass
    except Exception:
        pass
    return score, reasons


def _ocr_page_screenshot(url: str, timeout_ms: int = 15000) -> str:
    """Rende la pagina con Playwright e prova OCR sullo screenshot.

    Utile quando il testo della mail e' presente solo in immagini, canvas o
    contenuto renderizzato via JavaScript.
    """

    try:
        from PIL import Image
        import pytesseract
        from playwright.sync_api import sync_playwright
    except Exception:
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=USER_AGENT,
                locale="it-IT",
                viewport={"width": 1440, "height": 2200},
            )
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1000)
            img = page.screenshot(full_page=True)
            browser.close()
        from io import BytesIO

        def _open_image_safe(data: bytes) -> "Image.Image":
            im = Image.open(BytesIO(data))
            if im.mode == "P" and "transparency" in im.info:
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im).convert("RGB")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            return im

        screenshot = _open_image_safe(img)
        return pytesseract.image_to_string(screenshot, lang="ita")
    except Exception:
        return ""


def _browser_rendered_text(url: str, timeout_ms: int = 15000) -> str:
    """Restituisce il testo renderizzato dal browser reale.

    Serve per pagine che espongono i contatti solo dopo rendering JS oppure
    dentro pannelli/tabs che non compaiono nel markup statico scaricato via HTTP.
    """
    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image
    except Exception:
        return ""


def _browser_rendered_pairs(url: str, timeout_ms: int = 15000) -> list[tuple[str, str]]:
    """Renderizza la pagina con Playwright e recupera email con contesto DOM."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=USER_AGENT,
                locale="it-IT",
                viewport={"width": 1440, "height": 2200},
            )
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(800)
            html = page.content()
            browser.close()
        return _extract_emails_with_context(html)
    except Exception:
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=USER_AGENT,
                locale="it-IT",
                viewport={"width": 1440, "height": 2200},
            )
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(800)
            text = page.locator("body").inner_text(timeout=3000)
            browser.close()
            return text or ""
    except Exception:
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=USER_AGENT,
                locale="it-IT",
                viewport={"width": 1440, "height": 2200},
            )
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1000)
            img = page.screenshot(full_page=True)
            browser.close()
        from io import BytesIO

        def _open_image_safe(data: bytes) -> "Image.Image":
            im = Image.open(BytesIO(data))
            if im.mode == "P" and "transparency" in im.info:
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im).convert("RGB")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            return im

        screenshot = _open_image_safe(img)
        return pytesseract.image_to_string(screenshot, lang="ita")
    except Exception:
        return ""


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
        ctx_score, _reasons = _score_email_context(ctx, email, ctx)
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
                "servizio associato di polizia",
                "servizio associato di vigilanza",
                "comando p.m",
                "comando pl",
                "comando pm",
                "comando di polizia",
                "polizia urbana",
                "service de police",
                "service de police locale",
                "union des communes",
                "municipalite",
                "municipalité",
            ]
        ) or _best_context_similarity(ctx_l, PL_CONTEXT_CONCEPTS) >= 0.78
        if not (page_is_polizia or is_pl_local or is_pl_ctx or ctx_score >= 2):
            continue
        # escludi indirizzi personali e provider gratuiti se non chiaramente PL
        if is_likely_personal_email(email) and not (page_is_polizia or is_pl_local or is_pl_ctx or ctx_score >= 3):
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


def _enqueue_candidate_pages(
    frontier: list[tuple[int, str]],
    soup: BeautifulSoup,
    base: str,
    page_is_pl: bool,
    seen: set[str],
    min_score: int = 1,
    limit: int = 16,
) -> None:
    for url, score in _candidate_links(soup, base, page_is_pl=page_is_pl):
        if score < min_score or url in seen:
            continue
        seen.add(url)
        frontier.append((score, url))
        if len(frontier) >= limit:
            break


def _maybe_extract_pdfs(
    session: requests.Session,
    page_html: str,
    page_url: str,
    base: str,
    deadline: float,
    req_timeout: tuple[int, int],
    strict_pl_local: bool,
    pdf_extract: bool,
    accept_email,
    pec_all: set[str],
    mail_all: set[str],
) -> None:
    if not pdf_extract or (pec_all or mail_all):
        return
    from .pdf_extractor import extract_emails_from_pdf_url, find_pdf_links, find_pdf_links_broad

    pdfs = list(
        dict.fromkeys(
            find_pdf_links(page_html, page_url or base, limit=6)
            + find_pdf_links_broad(page_html, page_url or base, limit=8)
        )
    )[:10]
    for pdf_url in pdfs:
        if time.monotonic() > deadline or (pec_all or mail_all):
            break
        pairs = extract_emails_from_pdf_url(session, pdf_url, timeout=6)
        for em, ctx in pairs:
            accept_email(em, ctx, page_html, True, "pdf")


def scrape_polizia_locale(
    comune: str,
    provincia: str,
    codice_istat: str,
    site_hint: str = "",
    timeout: int = 15,
    total_budget: float = 40.0,
    max_candidates: int = 4,
    strict_pl_local: bool = True,
    pdf_extract: bool = True,
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

    seed_urls: list[str] = []
    if site_hint.strip():
        hint = site_hint.strip()
        hint_parsed = urlparse(hint if hint.startswith(("http://", "https://")) else "https://" + hint)
        if hint_parsed.netloc and hint_parsed.netloc == parsed.netloc and hint_parsed.path not in ("", "/"):
            seed_urls.append(hint_parsed.geturl())

    pages_visited: list[str] = []
    pec_all: set[str] = set()
    mail_all: set[str] = set()
    matched_by: set[str] = set()

    def _note(kind: str) -> None:
        matched_by.add(kind)

    def _accept_email(email: str, ctx: str, html: str, page_is_polizia: bool, source_kind: str) -> None:
        ctx_score, reasons = _score_email_context(html, email, ctx)
        local = email.split("@")[0].lower()
        is_pl_local = _is_strict_pl_local_email(email)
        is_pl_ctx = any(k in _normalize_match_text(ctx) for k in PL_HINTS)
        generic_local_parts = {
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
        }
        if strict_pl_local:
            if not is_pl_local and local in generic_local_parts:
                return
            if not (is_pl_local or page_is_polizia or is_pl_ctx or ctx_score >= 4):
                return
            if is_likely_personal_email(email) and not (is_pl_local or page_is_polizia or ctx_score >= 5):
                return
        else:
            if not (page_is_polizia or is_pl_local or is_pl_ctx or ctx_score >= 2):
                return
            if is_likely_personal_email(email) and not (is_pl_local or page_is_polizia or ctx_score >= 4):
                return
        if local in {"noreply", "no-reply", "webmaster", "postmaster"}:
            return
        if _is_pec(email, ctx):
            pec_all.add(email)
        else:
            mail_all.add(email)
        for reason in reasons:
            _note(reason)
        _note(source_kind)

    def _harvest(html: str, url_is_pl: bool, page_url: str = ""):
        found_before = bool(pec_all or mail_all)
        pairs = _extract_emails_with_context(html)
        for e, ctx in pairs:
            _accept_email(e, ctx, html, url_is_pl, "page_html")

        # Fallback: prima il testo renderizzato dal browser, poi lo screenshot OCR.
        if (not found_before) and not (pec_all or mail_all) and page_url:
            html_l = html.lower()
            if url_is_pl or page_url == site or "polizia" in html_l or "municipium" in html_l or "contatti" in html_l:
                rendered_pairs = _browser_rendered_pairs(page_url)
                for e, ctx in rendered_pairs:
                    _accept_email(e, ctx, ctx, url_is_pl, "js_render")

                if not rendered_pairs:
                    rendered_text = _browser_rendered_text(page_url)
                    if rendered_text:
                        for m in EMAIL_RE.finditer(rendered_text):
                            e = m.group(0)
                            ctx = rendered_text[max(0, m.start() - 80):m.end() + 80]
                            _accept_email(e, ctx, rendered_text, url_is_pl, "js_render")

                if not (pec_all or mail_all):
                    ocr_text = _ocr_page_screenshot(page_url)
                    if ocr_text:
                        for m in EMAIL_RE.finditer(ocr_text):
                            e = m.group(0)
                            ctx = ocr_text[max(0, m.start() - 80):m.end() + 80]
                            _accept_email(e, ctx, ocr_text, url_is_pl, "ocr")

    try:
        # 0) Se il chiamante ha passato una pagina ufficio specifica, la visitiamo
        # per prima: molte schede PA espongono i contatti solo lì.
        for url in seed_urls:
            if time.monotonic() > deadline or (pec_all or mail_all):
                break
            try:
                rr = session.get(url, timeout=req_timeout, allow_redirects=True)
                if rr.status_code != 200:
                    continue
                pages_visited.append(rr.url)
                _harvest(rr.text, url_is_pl=_path_is_polizia(rr.url) or _path_is_polizia(url), page_url=rr.url)
            except Exception:
                continue

        # 1) Path diretti
        if not (pec_all or mail_all):
            direct_urls = [base + path for path in _DIRECT_PATH_HINTS[: max(6, max_candidates * 2)]]
            fetched_direct = _fetch_many(session, direct_urls, req_timeout)
            for origin_url, status_code, final_url, text in fetched_direct:
                if time.monotonic() > deadline or (pec_all or mail_all):
                    break
                if not status_code or not text:
                    continue
                pages_visited.append(final_url)
                _harvest(text, url_is_pl=_path_is_polizia(final_url) or _path_is_polizia(origin_url), page_url=final_url)

        # 1b) Sitemap discovery: molti siti PA pubblicano le UO solo via sitemap
        if not (pec_all or mail_all):
            sitemap_seed_urls = [
                urljoin(base, "/robots.txt"),
                urljoin(base, "/sitemap.xml"),
                urljoin(base, "/sitemap_index.xml"),
            ]
            sitemap_urls: list[str] = []
            for seed_url in sitemap_seed_urls:
                if time.monotonic() > deadline or len(sitemap_urls) >= 8:
                    break
                try:
                    rr = session.get(seed_url, timeout=req_timeout, allow_redirects=True)
                    if rr.status_code != 200:
                        continue
                    text = rr.text or ""
                    if seed_url.endswith("robots.txt"):
                        for line in text.splitlines():
                            if line.lower().startswith("sitemap:"):
                                sitemap_urls.append(line.split(":", 1)[1].strip())
                    else:
                        for loc in re.findall(r"<loc>(.*?)</loc>", text, flags=re.IGNORECASE | re.DOTALL):
                            if loc.endswith((".xml", ".xml.gz")):
                                sitemap_urls.append(loc.strip())
                except Exception:
                    continue

            seen_sitemaps: set[str] = set()
            for sitemap_url in sitemap_urls:
                if time.monotonic() > deadline or (pec_all or mail_all):
                    break
                if sitemap_url in seen_sitemaps:
                    continue
                seen_sitemaps.add(sitemap_url)
                try:
                    rr = session.get(sitemap_url, timeout=req_timeout, allow_redirects=True)
                    if rr.status_code != 200:
                        continue
                    text = rr.text or ""
                    if not any(k in text.lower() for k in ("polizia", "vigili", "municipale", "comando", "sicurezza urbana")):
                        continue
                    for loc in re.findall(r"<loc>(.*?)</loc>", text, flags=re.IGNORECASE | re.DOTALL):
                        u = loc.strip()
                        if not u.startswith(("http://", "https://")):
                            continue
                        low_u = u.lower()
                        if not any(k in low_u for k in ("polizia", "vigili", "municipale", "comando", "sicurezza-urbana", "sicurezza urbana", "punto_contatto")):
                            continue
                        if time.monotonic() > deadline or (pec_all or mail_all):
                            break
                        try:
                            rr2 = session.get(u, timeout=req_timeout, allow_redirects=True)
                            if rr2.status_code != 200:
                                continue
                            pages_visited.append(rr2.url)
                            _harvest(rr2.text, url_is_pl=_path_is_polizia(rr2.url) or _path_is_polizia(u), page_url=rr2.url)
                        except Exception:
                            continue
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
                    _harvest(rr.text, url_is_pl=True, page_url=rr.url)
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

        # 3) Homepage + frontiera BFS prioritaria su più livelli
        home_html = ""
        home_url = site
        if not (pec_all or mail_all):
            try:
                r = session.get(site, timeout=req_timeout)
                pages_visited.append(r.url)
                home_html = r.text
                home_url = r.url
                soup = BeautifulSoup(r.text, "html.parser")
                _harvest(r.text, url_is_pl=False, page_url=r.url)

                frontier: list[tuple[int, str]] = []
                seen_frontier: set[str] = {r.url, site}
                _enqueue_candidate_pages(frontier, soup, base, False, seen_frontier, limit=max(16, max_candidates * 6))

                fetched_pages = 0
                while frontier and fetched_pages < 28 and time.monotonic() <= deadline and not (pec_all or mail_all):
                    frontier.sort(key=lambda item: (-item[0], item[1]))
                    batch = [url for _score, url in frontier[: min(6, len(frontier))]]
                    frontier = frontier[min(6, len(frontier)):]
                    fetched = _fetch_many(session, batch, req_timeout)
                    new_soup_pages: list[tuple[str, str, bool]] = []
                    for origin_url, status_code, final_url, text in fetched:
                        if time.monotonic() > deadline or (pec_all or mail_all):
                            break
                        if not status_code or not text:
                            continue
                        fetched_pages += 1
                        pages_visited.append(final_url)
                        is_pl = _path_is_polizia(final_url) or _path_is_polizia(origin_url)
                        _harvest(text, url_is_pl=is_pl, page_url=final_url)
                        new_soup_pages.append((final_url, text, is_pl))
                        if is_pl:
                            _maybe_extract_pdfs(
                                session=session,
                                page_html=text,
                                page_url=final_url,
                                base=base,
                                deadline=deadline,
                                req_timeout=req_timeout,
                                strict_pl_local=strict_pl_local,
                                pdf_extract=pdf_extract,
                                accept_email=_accept_email,
                                pec_all=pec_all,
                                mail_all=mail_all,
                            )
                    for page_url, text, is_pl in new_soup_pages:
                        if time.monotonic() > deadline or (pec_all or mail_all):
                            break
                        soup2 = BeautifulSoup(text, "html.parser")
                        _enqueue_candidate_pages(frontier, soup2, base, is_pl, seen_frontier, min_score=2 if is_pl else 1, limit=40)
            except Exception:
                pass

        # 4) Fallback largo: siti molto diversi o browser bloccato.
        if not (pec_all or mail_all):
            try:
                soup = BeautifulSoup(home_html or "", "html.parser") if home_html else BeautifulSoup("", "html.parser")
                if not home_html:
                    r = session.get(site, timeout=req_timeout)
                    soup = BeautifulSoup(r.text, "html.parser")
                    home_html = r.text
                    home_url = r.url
                broad_candidates = _broad_candidate_links(soup, base, limit=max(4, int(max_candidates) * 2), page_is_pl=True)
                fetched_broad = _fetch_many(session, broad_candidates, req_timeout)
                for origin_url, status_code, final_url, text in fetched_broad:
                    if time.monotonic() > deadline or (pec_all or mail_all):
                        break
                    if not status_code or not text:
                        continue
                    pages_visited.append(final_url)
                    is_pl = _path_is_polizia(final_url) or _path_is_polizia(origin_url)
                    _harvest(text, url_is_pl=is_pl, page_url=final_url)
                    if is_pl and pdf_extract and not (pec_all or mail_all):
                        _maybe_extract_pdfs(
                            session=session,
                            page_html=text,
                            page_url=final_url,
                            base=base,
                            deadline=deadline,
                            req_timeout=req_timeout,
                            strict_pl_local=strict_pl_local,
                            pdf_extract=pdf_extract,
                            accept_email=_accept_email,
                            pec_all=pec_all,
                            mail_all=mail_all,
                        )
                    if pec_all or mail_all:
                        break
            except Exception:
                pass
    except Exception:
        if not (pec_all or mail_all):
            return None

    if not pec_all and not mail_all:
        return None
    confidence = 0.45
    if matched_by:
        confidence += min(0.35, 0.05 * len(matched_by))
    if any(_path_is_polizia(p) for p in pages_visited):
        confidence += 0.1
    if any(is_pl_specific_email(e) for e in pec_all.union(mail_all)):
        confidence += 0.1
    
    # Estendi bonus per nuovi segnali di contesto
    matched_str = " ".join(sorted(matched_by))
    context_signals = ("context_polizia", "dom_parent_pl", "dom_heading_pl", "context_fuzzy_polizia", "sibling_context_pl", "anchor_context_pl", "section_context_pl")
    if any(k in matched_str for k in context_signals):
        # Bonus base per qualsiasi segnale
        confidence += 0.05
        # Bonus aggiuntivo se fuzzy o sibling context
        if any(k in matched_str for k in ("context_fuzzy_polizia", "sibling_context_pl")):
            confidence += 0.03
        # Bonus aggiuntivo se anchor o section context
        if any(k in matched_str for k in ("anchor_context_pl", "section_context_pl")):
            confidence += 0.02
    
    return ScrapeResult(
        comune=comune,
        codice_istat=codice_istat,
        pec=" | ".join(sorted(pec_all)),
        email=" | ".join(sorted(mail_all)),
        sito=base,
        pagina=pages_visited[-1] if pages_visited else "",
        confidence=min(confidence, 0.99),
        matched_by=" | ".join(sorted(matched_by)),
    )
