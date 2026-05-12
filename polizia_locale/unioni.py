"""Gestione delle Unioni di Comuni / Comunità Montane / Consorzi di Polizia Locale.

Quando la Polizia Locale è gestita in forma associata, la UO/AOO PL è
registrata in IndicePA sotto il Codice_IPA dell'Unione, non dei singoli
comuni membri. Questo modulo:

  1. Identifica le UO/AOO PL appartenenti a un ente "L18 Unione di Comuni",
     "L12 Comunità Montana" o "L36 Consorzio fra enti".
  2. Scrapa il sito istituzionale dell'Unione alla ricerca dell'elenco dei
     comuni aderenti.
  3. Restituisce, per ogni Unione, l'insieme dei codici ISTAT dei comuni
     della regione che ne fanno parte.

Il chiamante può così replicare la PEC su tutti i membri.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import pandas as pd
from bs4 import BeautifulSoup

from .indicepa import (
    _is_polizia_locale,
    _extract_mails,
    _load_aoo_df,
    _load_uo_df,
)
from .scraper import _new_session

# Categorie IPA che indicano un ente intercomunale.
SHARED_SERVICE_CATEGORIES = {"L18", "L12", "L36"}


@dataclass
class UnionePLRecord:
    codice_ipa: str
    denominazione_ente: str
    categoria: str
    descrizione_uo: str
    pec: str
    email: str
    telefono: str
    indirizzo: str
    cap: str
    sito: str
    fonte: str = "IndicePA-Unione"


def _strip(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def find_unioni_with_polizia_locale(region_code: str | None = None) -> list[UnionePLRecord]:
    """Ritorna gli enti L18/L12/L36 che hanno una UO o AOO di Polizia Locale.

    Se `region_code` è specificato, filtra solo gli enti la cui sede istituzionale
    è nella stessa regione (evita falsi positivi cross-regione).
    """
    from .indicepa import download_enti
    download_enti()
    enti = pd.read_excel(
        "/root/.cache/polizia_locale/indicepa_enti.xlsx",
        dtype=str,
        engine="openpyxl",
    ).fillna("")
    enti.columns = [c.strip() for c in enti.columns]
    enti = enti[enti["Codice_Categoria"].str.upper().isin(SHARED_SERVICE_CATEGORIES)]

    if region_code:
        # filtra per appartenenza alla regione tramite Codice_comune_ISTAT della sede
        from .comuni import _load_csv, _pick_column
        df_istat = _load_csv()
        col_region = _pick_column(df_istat, ["Codice Regione", "Codice regione"])
        col_comune = _pick_column(
            df_istat, ["Codice Comune formato alfanumerico", "Codice Comune"]
        )
        region_istat = set(
            df_istat[df_istat[col_region].astype(str).str.zfill(2) == str(region_code).zfill(2)][
                col_comune
            ]
            .astype(str)
            .str.zfill(6)
            .tolist()
        )
        if "Codice_comune_ISTAT" in enti.columns:
            enti = enti[
                enti["Codice_comune_ISTAT"]
                .astype(str)
                .str.strip()
                .str.zfill(6)
                .isin(region_istat)
            ]

    shared_ipa = set(enti["Codice_IPA"].str.strip())

    # Info ente per IPA
    info_by_ipa: dict[str, dict] = {}
    for _, row in enti.iterrows():
        info_by_ipa[row["Codice_IPA"].strip()] = {
            "denominazione": row["Denominazione_ente"].strip(),
            "categoria": row["Codice_Categoria"].strip().upper(),
            "sito": row.get("Sito_istituzionale", "").strip(),
        }

    out: list[UnionePLRecord] = []
    seen: set[str] = set()

    # UO
    df_uo = _load_uo_df()
    df_uo = df_uo[df_uo["Codice_IPA"].isin(shared_ipa)]
    df_uo = df_uo[df_uo["Descrizione_uo"].apply(_is_polizia_locale)]
    for _, row in df_uo.iterrows():
        pec, mail = _extract_mails(row)
        if not pec and not mail:
            continue
        ipa = row["Codice_IPA"].strip()
        info = info_by_ipa.get(ipa, {})
        key = (ipa, pec, mail)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            UnionePLRecord(
                codice_ipa=ipa,
                denominazione_ente=info.get("denominazione", row.get("Denominazione_ente", "")),
                categoria=info.get("categoria", ""),
                descrizione_uo=str(row.get("Descrizione_uo", "")).strip(),
                pec=pec,
                email=mail,
                telefono=str(row.get("Telefono", "")).strip(),
                indirizzo=str(row.get("Indirizzo", "")).strip(),
                cap=str(row.get("CAP", "")).strip(),
                sito=info.get("sito", ""),
            )
        )

    # AOO
    try:
        df_aoo = _load_aoo_df()
        df_aoo = df_aoo[df_aoo["Codice_IPA"].isin(shared_ipa)]
        df_aoo = df_aoo[df_aoo["Denominazione_aoo"].apply(_is_polizia_locale)]
        for _, row in df_aoo.iterrows():
            pec, mail = _extract_mails(row)
            if not pec and not mail:
                continue
            ipa = row["Codice_IPA"].strip()
            info = info_by_ipa.get(ipa, {})
            key = (ipa, pec, mail)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                UnionePLRecord(
                    codice_ipa=ipa,
                    denominazione_ente=info.get("denominazione", row.get("Denominazione_ente", "")),
                    categoria=info.get("categoria", ""),
                    descrizione_uo=str(row.get("Denominazione_aoo", "")).strip(),
                    pec=pec,
                    email=mail,
                    telefono=str(row.get("Telefono", "")).strip(),
                    indirizzo=str(row.get("Indirizzo", "")).strip(),
                    cap=str(row.get("CAP", "")).strip(),
                    sito=info.get("sito", ""),
                )
            )
    except Exception:
        pass

    return out


# Pagine candidate sul sito di una Unione dove cercare la lista dei comuni.
# Prima si tentano quelle dedicate (es. /comuni, /territorio): solo se nessuna
# di queste restituisce match, si ricade sulla homepage.
_MEMBER_PATHS_DEDICATED = (
    "/comuni",
    "/i-comuni",
    "/comuni-aderenti",
    "/comuni-dell-unione",
    "/comuni-dellunione",
    "/comuni-membri",
    "/enti-aderenti",
    "/territorio",
    "/territorio/comuni",
    "/lunione/comuni",
    "/amministrazione/comuni",
    "/aree/comuni",
    "/chi-siamo",
    "/lunione",
)
_MEMBER_PATHS_FALLBACK = ("/",)


def fetch_member_comuni(sito: str, timeout: int = 10) -> tuple[str, str]:
    """Scrapa il sito di una Unione e ritorna due chunk di testo:
    `dedicated_text` (pagine come /comuni, /territorio…) e `homepage_text`.
    Il caller fa il match prima sul testo dedicato; se non trova niente, ricade
    sulla homepage (che può contenere rumore).
    """
    if not sito:
        return "", ""
    if not sito.startswith("http"):
        sito = "https://" + sito
    parsed = urlparse(sito)
    base = f"{parsed.scheme}://{parsed.netloc}"
    sess = _new_session()

    def _scrape(paths, max_visits):
        chunks: list[str] = []
        visited = 0
        seen: set[str] = set()
        for path in paths:
            if visited >= max_visits:
                break
            url = urljoin(base + "/", path.lstrip("/")) if path != "/" else base + "/"
            if url in seen:
                continue
            seen.add(url)
            try:
                r = sess.get(url, timeout=(5, timeout), allow_redirects=True)
                if r.status_code != 200:
                    continue
                visited += 1
                soup = BeautifulSoup(r.text, "html.parser")
                chunks.append(soup.get_text(" ", strip=True))
            except Exception:
                continue
        return " \n ".join(chunks)

    dedicated = _scrape(_MEMBER_PATHS_DEDICATED, 5)
    homepage = _scrape(_MEMBER_PATHS_FALLBACK, 1)
    return dedicated, homepage


def match_member_comuni(page_text: str, comuni: list) -> list:
    """Dato il testo concatenato delle pagine dell'Unione, ritorna i Comune
    della regione il cui nome compare nel testo (match insensibile a
    maiuscole/accenti, con boundaries di parola).
    """
    if not page_text or not comuni:
        return []
    text_n = _strip(page_text)
    out: list = []
    seen: set[str] = set()
    for c in comuni:
        if c.codice_istat in seen:
            continue
        name_n = _strip(c.nome)
        if len(name_n) < 3:
            continue
        # boundary di parola (semplificato: spazio o inizio/fine + char non alfa)
        pattern = r"(?<![a-z0-9])" + re.escape(name_n) + r"(?![a-z0-9])"
        if re.search(pattern, text_n):
            out.append(c)
            seen.add(c.codice_istat)
    return out
