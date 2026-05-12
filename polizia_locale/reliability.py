"""Verifica affidabilita: conferma mail/PEC sul sito ufficiale del comune."""
from __future__ import annotations

from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


def _same_host(a: str, b: str) -> bool:
    ha = urlparse(a).netloc.lower().lstrip("www.")
    hb = urlparse(b).netloc.lower().lstrip("www.")
    return bool(ha and hb and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)))


def _normalize_site_url(site: str) -> str:
    site = (site or "").strip()
    if not site:
        return ""
    if not site.startswith(("http://", "https://")):
        site = "https://" + site
    return site


def verify_emails_on_site(
    site: str,
    emails: set[str],
    timeout: int = 10,
    max_pages: int = 8,
) -> set[str]:
    """Conferma quali email sono realmente presenti sul sito ufficiale."""
    base = _normalize_site_url(site)
    if not base or not emails:
        return set()

    target = {e.lower() for e in emails if "@" in e}
    if not target:
        return set()

    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (compatible; PoliziaLocaleVerifier/1.0)"

    seeds = [
        base,
        urljoin(base, "/contatti"),
        urljoin(base, "/amministrazione"),
        urljoin(base, "/polizia-locale"),
        urljoin(base, "/polizia-municipale"),
        urljoin(base, "/comando-polizia-locale"),
        urljoin(base, "/comando-polizia-municipale"),
        urljoin(base, "/vigili-urbani"),
        urljoin(base, "/ufficio-polizia-locale"),
        urljoin(base, "/uffici"),
    ]

    kw = ("contatt", "polizia", "vigili", "pec", "email", "comando", "municipale")
    queue: deque[str] = deque(seeds)
    visited: set[str] = set()
    confirmed: set[str] = set()

    while queue and len(visited) < max_pages and confirmed != target:
        url = queue.popleft()
        if not url or url in visited:
            continue
        visited.add(url)

        try:
            r = sess.get(url, timeout=(min(timeout, 6), timeout), allow_redirects=True)
            if r.status_code != 200:
                continue
        except Exception:
            continue

        html = r.text or ""
        low = html.lower()
        for e in target:
            if e in low:
                confirmed.add(e)

        if confirmed == target:
            break

        try:
            soup = BeautifulSoup(html, "html.parser")
            priority, normal = [], []
            for a in soup.find_all("a", href=True)[:120]:
                href = a.get("href", "").strip()
                if not href:
                    continue
                nxt = urljoin(r.url, href)
                if not nxt.startswith(("http://", "https://")):
                    continue
                if not _same_host(base, nxt):
                    continue
                txt = (a.get_text(" ", strip=True) + " " + href).lower()
                if any(k in txt for k in kw):
                    priority.append(nxt)
                else:
                    normal.append(nxt)

            for nxt in priority + normal:
                if nxt not in visited and nxt not in queue:
                    queue.append(nxt)
        except Exception:
            continue

    try:
        sess.close()
    except Exception:
        pass

    # ritorna casing originale (se trovato)
    return {e for e in emails if e.lower() in confirmed}
