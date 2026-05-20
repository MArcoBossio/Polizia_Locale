"""Ricerca Polizia Locale/Municipale tramite il dataset IndicePA (Unità Organizzative)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .utils import cached_path, download

UO_XLSX_URL = (
    "https://indicepa.gov.it/ipa-dati/dataset/"
    "c8d2e2b3-a9f1-4123-bc8b-26315ed20fce/resource/"
    "b0aa1f6c-f135-4c8a-b416-396fed4e1a5d/download/unita-organizzative.xlsx"
)

AOO_XLSX_URL = (
    "https://indicepa.gov.it/ipa-dati/dataset/"
    "15c8cc52-749a-4f64-9bd1-9c3bb4f6f3df/resource/"
    "cdaded04-f84e-4193-a720-47d6d5f422aa/download/aree-organizzative-omogenee.xlsx"
)

ENTI_XLSX_URL = (
    "https://indicepa.gov.it/ipa-dati/dataset/"
    "5baa3eb8-266e-455a-8de8-b1f434c279b2/resource/"
    "d09adf99-dc10-4349-8c53-27b1e5aa97b6/download/enti.xlsx"
)

# Pattern che identificano la Polizia Locale / Municipale.
# `_SEP` accetta spazio, underscore, punto o trattino tra le parole, perché
# IndicePA registra alcune UO come "Polizia_Municipale", "Polizia.Locale",
# "Polizia-Locale" ecc.
_SEP = r"[\s._\-]+"
_PATTERN = re.compile(
    r"(?<!\w)("
    + r"polizi[ae]" + _SEP + r"(local[ei]|municipal[ei]|urban[ei])"
    + r"|corpo(" + _SEP + r"di)?" + _SEP + r"polizia" + _SEP + r"(local[ei]|municipal[ei])"
    + r"|comando(" + _SEP + r"di)?" + _SEP + r"polizia" + _SEP + r"(local[ei]|municipal[ei])"
    + r"|comando(" + _SEP + r"di)?" + _SEP + r"vigili" + _SEP + r"urbani"
    + r"|vigili" + _SEP + r"urbani"
    + r"|polizia" + _SEP + r"(loc|mun)\.?"
    + r")(?!\w)",
    re.IGNORECASE,
)

# Esclusioni: evitiamo descrizioni che parlano di "polizia di stato",
# "polizia stradale", "polizia provinciale", "polizia mortuaria" ecc.
_EXCLUDE = re.compile(
    r"(?<!\w)(polizi[ae]" + _SEP + r"(di" + _SEP + r"stato|stradale|provincial[ei]|mortuari[ae]|giudiziari[ae]|scientifica|amministrativ[ae]|penitenziari[ae]))(?!\w)",
    re.IGNORECASE,
)


@dataclass
class PoliziaLocaleRecord:
    codice_istat: str
    comune: str
    codice_ipa: str
    denominazione_ente: str
    codice_uni_uo: str
    descrizione_uo: str
    pec: str
    email: str
    telefono: str
    indirizzo: str
    cap: str
    fonte: str = "IndicePA"

    def as_dict(self) -> dict:
        return {
            "comune": self.comune,
            "codice_istat": self.codice_istat,
            "codice_ipa": self.codice_ipa,
            "denominazione_ente": self.denominazione_ente,
            "codice_uni_uo": self.codice_uni_uo,
            "descrizione_uo": self.descrizione_uo,
            "pec": self.pec,
            "email": self.email,
            "telefono": self.telefono,
            "indirizzo": self.indirizzo,
            "cap": self.cap,
            "fonte": self.fonte,
        }


def _strip_accents(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _is_polizia_locale(descrizione: str) -> bool:
    if not isinstance(descrizione, str) or not descrizione.strip():
        return False
    text = _strip_accents(descrizione)
    if _EXCLUDE.search(text):
        return False
    return bool(_PATTERN.search(text))


def download_uo() -> str:
    path = cached_path("indicepa_uo.xlsx")
    if not path.exists():
        print("      Scaricando dataset UO da IndicePA (14+ MB, attendere...)...") 
    download(UO_XLSX_URL, path, max_age_hours=24)
    return str(path)


def download_aoo() -> str:
    path = cached_path("indicepa_aoo.xlsx")
    if not path.exists():
        print("      Scaricando dataset AOO da IndicePA (6+ MB, attendere)...")
    download(AOO_XLSX_URL, path, max_age_hours=24)
    return str(path)


def download_enti() -> str:
    path = cached_path("indicepa_enti.xlsx")
    download(ENTI_XLSX_URL, path, max_age_hours=24)
    return str(path)


def _load_uo_df() -> pd.DataFrame:
    path = Path(download_uo())
    csv_path = cached_path("indicepa_uo.csv")
    
    # Se il CSV cached esiste ed è più recente dell'Excel, usalo
    if csv_path.exists() and csv_path.stat().st_mtime > path.stat().st_mtime:
        df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    else:
        # Carica da Excel e cachea come CSV per prossimi usi
        print("      Convertendo UO da Excel a CSV (primo utilizzo, attendere)...")
        df = pd.read_excel(str(path), dtype=str, engine="openpyxl")
        df.to_csv(csv_path, index=False)
    
    df.columns = [c.strip() for c in df.columns]
    return df.fillna("")


def _load_aoo_df() -> pd.DataFrame:
    path = Path(download_aoo())
    csv_path = cached_path("indicepa_aoo.csv")
    
    # Se il CSV cached esiste ed è più recente dell'Excel, usalo
    if csv_path.exists() and csv_path.stat().st_mtime > path.stat().st_mtime:
        df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    else:
        # Carica da Excel e cachea come CSV per prossimi usi
        print("      Convertendo AOO da Excel a CSV (primo utilizzo, attendere)...")
        df = pd.read_excel(str(path), dtype=str, engine="openpyxl")
        df.to_csv(csv_path, index=False)
    
    df.columns = [c.strip() for c in df.columns]
    return df.fillna("")


_PL_LOCAL_PARTS = (
    "polizialocale",
    "poliziamunicipale",
    "polizia-municipale",
    "polizia.locale",
    "polizia.municipale",
    "polizia-locale",
    "polizia-municipale",
    "polizia_locale",
    "polizia_municipale",
    "pol.locale",
    "pol.municipale",
    "vigili",
    "vigiliurbani",
    "vigili.urbani",
    "vigili-urbani",
    "comandopm",
    "comandopl",
    "comando.pm",
    "comando.pl",
    "comando-pm",
    "comando-pl",
    "comando.polizia",
    "comando-polizia",
    "comando_polizia",
)


def is_pl_specific_email(email: str) -> bool:
    """True se la local-part dell'email indica una casella della Polizia Locale.

    Accetta:
      - polizialocale@…, poliziamunicipale@…, polizia.locale@…, polizia-municipale@…
      - vigili@…, vigili.urbani@…, vigili-urbani@…, vigiliurbani@…
      - comandopm@…, comandopl@…, comando.pm@…, comando-polizia@…
      - pm.<comune>@…, pl.<comune>@…
      - <prefix>pm@…, <prefix>pl@… (es. centraleoperativapm@…, ufficiopm@…)
      - info@…, segreteria@… (quando indicano la Polizia Locale)
    Rifiuta:
      - comune.X@postacert…, protocollo@…, ecc.
    """
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower().strip()
    if any(k in local for k in _PL_LOCAL_PARTS):
        return True
    # pattern pm.* / pl.*
    if local.startswith(("pm.", "pl.", "pm_", "pl_", "pm-", "pl-")):
        return True
    # pattern *.pm / *_pm / *-pm / *pm  (alla fine, anche senza separatore)
    if re.search(r"(?:^|[._\-]?)(?:pm|pl)$", local):
        return True
    # accetta info@ e segreteria@ come email di Polizia Locale
    if local in ("info", "segreteria"):
        return True
    return False


def _extract_mails(row: pd.Series, only_pl_specific: bool = False) -> tuple[str, str]:
    """Restituisce (pec, mail_ordinaria) dalle 5 coppie Mail/Tipo_Mail.

    Se `only_pl_specific=True` tiene solo mail con local-part PL-specifica
    (polizialocale@, vigili@, comandopm@, …). Le PEC generiche del Comune
    (es. comune.X@postacert.regione.it) vengono scartate.
    """
    pec, ordinaria = [], []
    for i in range(1, 6):
        mail = str(row.get(f"Mail{i}", "")).strip()
        tipo = str(row.get(f"Tipo_Mail{i}", "")).strip().lower()
        if not mail or "@" not in mail:
            continue
        if only_pl_specific and not is_pl_specific_email(mail):
            continue
        if tipo == "pec":
            pec.append(mail)
        else:
            ordinaria.append(mail)
    return (
        " | ".join(dict.fromkeys(pec)),
        " | ".join(dict.fromkeys(ordinaria)),
    )


def find_polizia_locale_uo(
    istat_codes: list[str], strict: bool = True
) -> list[PoliziaLocaleRecord]:
    """Filtra le UO di IndicePA che sono Polizia Locale/Municipale per i comuni dati.

    Se `strict=True` (default), tiene solo le UO che hanno almeno una mail/PEC
    con local-part PL-specifica (polizialocale@, vigili@, comandopm@, ecc.);
    le UO che hanno solo la PEC generica del Comune vengono scartate.
    """
    df = _load_uo_df()
    target = {c.zfill(6) for c in istat_codes if c}
    if "Codice_comune_ISTAT" not in df.columns:
        raise RuntimeError(
            "Colonna 'Codice_comune_ISTAT' non trovata nel dataset IndicePA UO. "
            f"Colonne: {list(df.columns)[:20]}"
        )
    df["_istat"] = df["Codice_comune_ISTAT"].astype(str).str.strip().str.zfill(6)
    df = df[df["_istat"].isin(target)]
    df = df[df["Descrizione_uo"].apply(_is_polizia_locale)]

    out: list[PoliziaLocaleRecord] = []
    for _, row in df.iterrows():
        pec, mail = _extract_mails(row, only_pl_specific=strict)
        if strict and not pec and not mail:
            continue  # in strict scarto le UO che hanno solo mail generiche
        out.append(
            PoliziaLocaleRecord(
                codice_istat=row["_istat"],
                comune="",  # popolato dal chiamante
                codice_ipa=str(row.get("Codice_IPA", "")).strip(),
                denominazione_ente=str(row.get("Denominazione_ente", "")).strip(),
                codice_uni_uo=str(row.get("Codice_uni_uo", "")).strip(),
                descrizione_uo=str(row.get("Descrizione_uo", "")).strip(),
                pec=pec,
                email=mail,
                telefono=str(row.get("Telefono", "")).strip(),
                indirizzo=str(row.get("Indirizzo", "")).strip(),
                cap=str(row.get("CAP", "")).strip(),
                fonte="IndicePA",
            )
        )
    return out


def find_polizia_locale_aoo(
    istat_codes: list[str], strict: bool = True
) -> list[PoliziaLocaleRecord]:
    """Filtra le AOO di IndicePA che sono Polizia Locale/Municipale per i comuni dati."""
    try:
        df = _load_aoo_df()
    except Exception:
        return []
    target = {c.zfill(6) for c in istat_codes if c}
    if "Codice_comune_ISTAT" not in df.columns:
        return []
    df["_istat"] = df["Codice_comune_ISTAT"].astype(str).str.strip().str.zfill(6)
    df = df[df["_istat"].isin(target)]
    df = df[df["Denominazione_aoo"].apply(_is_polizia_locale)]

    out: list[PoliziaLocaleRecord] = []
    for _, row in df.iterrows():
        pec, mail = _extract_mails(row, only_pl_specific=strict)
        if not pec and not mail:
            continue
        out.append(
            PoliziaLocaleRecord(
                codice_istat=row["_istat"],
                comune="",
                codice_ipa=str(row.get("Codice_IPA", "")).strip(),
                denominazione_ente=str(row.get("Denominazione_ente", "")).strip(),
                codice_uni_uo=str(row.get("Codice_uni_aoo", "")).strip(),
                descrizione_uo=str(row.get("Denominazione_aoo", "")).strip(),
                pec=pec,
                email=mail,
                telefono=str(row.get("Telefono", "")).strip(),
                indirizzo=str(row.get("Indirizzo", "")).strip(),
                cap=str(row.get("CAP", "")).strip(),
                fonte="IndicePA-AOO",
            )
        )
    return out


def load_enti_index() -> dict[str, dict]:
    """Indice Codice_IPA -> info ente (utile per trovare il sito istituzionale).

    Filtra solo gli enti con Codice_Categoria 'L6' (Comuni) per evitare collisioni
    con scuole / ASL / altri enti che condividono lo stesso Codice_comune_ISTAT.
    Include anche le PEC istituzionali (Mail1..Mail5) come fallback opzionale.
    """
    path = download_enti()
    df = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    df.columns = [c.strip() for c in df.columns]
    if "Codice_Categoria" in df.columns:
        df = df[df["Codice_Categoria"].str.strip().str.upper() == "L6"]
    idx: dict[str, dict] = {}
    site_col = None
    for cand in ("Sito_istituzionale", "Sito_Istituzionale", "Sito"):
        if cand in df.columns:
            site_col = cand
            break
    istat_col = None
    for cand in ("Codice_comune_ISTAT", "Codice_Comune_ISTAT", "Codice_ISTAT"):
        if cand in df.columns:
            istat_col = cand
            break
    for _, row in df.iterrows():
        cod = str(row.get("Codice_IPA", "")).strip()
        if not cod:
            continue
        pec_list: list[str] = []
        mail_list: list[str] = []
        for i in range(1, 6):
            m = str(row.get(f"Mail{i}", "")).strip()
            t = str(row.get(f"Tipo_Mail{i}", "")).strip().lower()
            if not m or "@" not in m:
                continue
            if t == "pec":
                pec_list.append(m)
            else:
                mail_list.append(m)
        idx[cod] = {
            "denominazione": str(row.get("Denominazione_ente", "")).strip(),
            "sito": str(row.get(site_col, "")).strip() if site_col else "",
            "codice_istat": str(row.get(istat_col, "")).strip().zfill(6) if istat_col else "",
            "tipologia": str(row.get("Tipologia", "")).strip(),
            "indirizzo": str(row.get("Indirizzo", "")).strip(),
            "cap": str(row.get("CAP", "")).strip(),
            "pec_comune": " | ".join(dict.fromkeys(pec_list)),
            "mail_comune": " | ".join(dict.fromkeys(mail_list)),
        }
    return idx


def build_enti_linkage_by_istat() -> dict[str, dict]:
    """Ritorna un indice normalizzato per Codice ISTAT con sito e PEC del comune.

    Questo è il punto unico da usare quando serve collegare:
    comune -> ente IPA -> sito istituzionale -> PEC/mail del Comune.
    """
    idx = load_enti_index()
    by_istat: dict[str, dict] = {}
    for info in idx.values():
        ci = str(info.get("codice_istat", "")).strip().zfill(6)
        if not ci:
            continue
        current = by_istat.get(ci)
        if current is None:
            by_istat[ci] = dict(info)
            continue
        # preferisci dati completi: sito, pec, mail
        for key in ("sito", "pec_comune", "mail_comune", "denominazione", "indirizzo", "cap"):
            if not current.get(key) and info.get(key):
                current[key] = info.get(key)
    return by_istat


def build_site_index_by_istat() -> dict[str, str]:
    linkage = build_enti_linkage_by_istat()
    return {ci: str(info.get("sito", "")).strip() for ci, info in linkage.items() if info.get("sito")}
