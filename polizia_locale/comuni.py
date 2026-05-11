"""Caricamento elenco comuni ISTAT, filtrato per regione."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .utils import cached_path, download

ISTAT_URL = (
    "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
)


@dataclass
class Comune:
    nome: str
    codice_istat: str  # 6 cifre
    provincia: str
    sigla_provincia: str
    regione: str

    def as_dict(self) -> dict:
        return {
            "comune": self.nome,
            "codice_istat": self.codice_istat,
            "provincia": self.provincia,
            "sigla_provincia": self.sigla_provincia,
            "regione": self.regione,
        }


def _load_csv() -> pd.DataFrame:
    path = cached_path("istat_comuni.csv")
    download(ISTAT_URL, path, max_age_hours=24 * 7)

    # Il file ISTAT usa ; come separatore e una codifica ANSI (cp1252/latin1).
    last_err = None
    for enc in ("latin-1", "cp1252", "utf-8"):
        try:
            return pd.read_csv(path, sep=";", dtype=str, encoding=enc, on_bad_lines="skip")
        except Exception as e:  # pragma: no cover
            last_err = e
    raise RuntimeError(f"Impossibile leggere il CSV ISTAT: {last_err}")


def _pick_column(df: pd.DataFrame, candidates: list[str]) -> str:
    for c in candidates:
        for col in df.columns:
            if col.strip().lower() == c.strip().lower():
                return col
    # match per prefisso
    for c in candidates:
        for col in df.columns:
            if col.strip().lower().startswith(c.strip().lower()):
                return col
    raise KeyError(f"Colonne ISTAT non trovate per: {candidates}. Disponibili: {list(df.columns)}")


def load_comuni(region_code: str) -> list[Comune]:
    """Ritorna l'elenco dei comuni della regione indicata (codice ISTAT 2 cifre)."""
    df = _load_csv()
    col_region_code = _pick_column(df, ["Codice Regione", "Codice regione"])
    col_region_name = _pick_column(
        df, ["Denominazione Regione", "Denominazione regione"]
    )
    col_comune = _pick_column(
        df,
        [
            "Denominazione in italiano",
            "Denominazione (Italiana e straniera)",
            "Denominazione",
        ],
    )
    col_codice_comune = _pick_column(
        df, ["Codice Comune formato alfanumerico", "Codice Comune", "Codice Istat del Comune"]
    )
    col_prov_name = _pick_column(
        df,
        [
            "Denominazione dell'Unità territoriale sovracomunale",
            "Denominazione Provincia",
            "Provincia",
        ],
    )
    col_sigla = _pick_column(df, ["Sigla automobilistica", "Sigla"])

    code = str(region_code).zfill(2)
    mask = df[col_region_code].astype(str).str.zfill(2) == code
    sub = df.loc[mask].copy()

    comuni: list[Comune] = []
    for _, row in sub.iterrows():
        codice = str(row[col_codice_comune]).strip()
        if codice and codice.isdigit():
            codice = codice.zfill(6)
        comuni.append(
            Comune(
                nome=str(row[col_comune]).strip(),
                codice_istat=codice,
                provincia=str(row[col_prov_name]).strip(),
                sigla_provincia=str(row[col_sigla]).strip(),
                regione=str(row[col_region_name]).strip(),
            )
        )
    return comuni
