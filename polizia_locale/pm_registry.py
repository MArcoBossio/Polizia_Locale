"""Fonte opzionale: directory enti su poliziamunicipale.it.

Recupera la scheda del comando per comune/provincia e prova a estrarre mail/PEC.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .indicepa import is_pl_specific_email
from .scraper import EMAIL_RE, _is_pec
from .utils import is_likely_personal_email

BASE_URL = "https://www.poliziamunicipale.it"


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


class PoliziaMunicipaleFinder:
    def __init__(self, timeout: int = 12):
        self.timeout = timeout
        self._sess = requests.Session()
        self._sess.headers["User-Agent"] = "Mozilla/5.0 (compatible; PoliziaLocaleBot/1.0)"
        self._letter_cache: dict[str, str] = {}
        self._detail_cache: dict[tuple[str, str], str] = {}

    def close(self) -> None:
        try:
            self._sess.close()
        except Exception:
            pass

    def _fetch(self, url: str) -> str:
        try:
            r = self._sess.get(url, timeout=self.timeout, allow_redirects=True)
            if r.status_code != 200:
                return ""
            return r.text
        except Exception:
            return ""

    def _fetch_letter_index(self, letter: str) -> str:
        if letter in self._letter_cache:
            return self._letter_cache[letter]
        html = self._fetch(f"{BASE_URL}/comuni?f={letter}")
        if html:
            self._letter_cache[letter] = html
        return html

    def _find_detail_url(self, comune: str, provincia: str) -> str:
        letter = (comune.strip()[:1] or "").upper()
        if not letter.isalpha():
            letter = "A"
        cache_key = (comune.strip().lower(), provincia.strip().lower())
        if cache_key in self._detail_cache:
            return self._detail_cache[cache_key]

        html = self._fetch_letter_index(letter)
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        target_comune = _norm(comune)
        target_prov = _norm(provincia)

        table = soup.find("table")
        if not table:
            return ""

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            a = cells[0].find("a")
            if not a or not a.get("href"):
                continue

            city = _norm(cells[1].get_text(" ", strip=True))
            prov = _norm(cells[2].get_text(" ", strip=True))

            if city != target_comune:
                continue
            # match morbido provincia ("Bolzano" vs "Bolzano/Bozen")
            if target_prov and target_prov not in prov and prov not in target_prov:
                continue

            detail_url = urljoin(BASE_URL, a["href"])
            self._detail_cache[cache_key] = detail_url
            return detail_url

        return ""

    def search_polizia_locale(
        self,
        comune: str,
        provincia: str = "",
        strict_pl_local: bool = True,
        allow_non_pl_fallback: bool = False,
    ) -> tuple[set[str], set[str], str]:
        """Ritorna (pec_set, mail_set, source_url).

        If `allow_non_pl_fallback` is True, non-PL-specific emails found on the
        poliziamunicipale.it scheda will be accepted as fallback unless their
        surrounding context indicates unrelated departments (anagrafe, ragioneria, etc.).
        """
        detail_url = self._find_detail_url(comune, provincia)
        if not detail_url:
            return set(), set(), ""

        html = self._fetch(detail_url)
        if not html:
            return set(), set(), detail_url

        pec: set[str] = set()
        mail: set[str] = set()

        # estrazione da testo pagina e mailto
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        haystack = "\n".join([html, text])
        UNWANTED = (
            "anagrafe",
            "ragioner",
            "ragioneria",
            "tribut",
            "protocollo",
            "segreteria",
            "personale",
            "econom",
            "finanzi",
        )

        for m in EMAIL_RE.finditer(haystack):
            email = m.group(0)
            is_pl = is_pl_specific_email(email)
            if strict_pl_local and not is_pl:
                continue
            # scarta indirizzi personali/di provider gratuiti se non PL-specifici
            if is_likely_personal_email(email) and not is_pl:
                continue
            start = max(0, m.start() - 80)
            end = min(len(haystack), m.end() + 80)
            ctx = haystack[start:end].lower()

            # fallback permissivo: accetta mail non-PL se non provengono da uffici
            # chiaramente non rilevanti (anagrafe, ragioneria, tributi, ecc.).
            if not is_pl and allow_non_pl_fallback:
                if any(k in ctx for k in UNWANTED):
                    continue

            if _is_pec(email, ctx):
                pec.add(email)
            else:
                mail.add(email)

        return pec, mail, detail_url
