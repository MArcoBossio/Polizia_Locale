"""Elenco ufficiale delle 20 regioni italiane (denominazione e codice ISTAT)."""

REGIONS = [
    ("01", "Piemonte"),
    ("02", "Valle d'Aosta/Vallée d'Aoste"),
    ("03", "Lombardia"),
    ("04", "Trentino-Alto Adige/Südtirol"),
    ("05", "Veneto"),
    ("06", "Friuli-Venezia Giulia"),
    ("07", "Liguria"),
    ("08", "Emilia-Romagna"),
    ("09", "Toscana"),
    ("10", "Umbria"),
    ("11", "Marche"),
    ("12", "Lazio"),
    ("13", "Abruzzo"),
    ("14", "Molise"),
    ("15", "Campania"),
    ("16", "Puglia"),
    ("17", "Basilicata"),
    ("18", "Calabria"),
    ("19", "Sicilia"),
    ("20", "Sardegna"),
]


def _normalize(name: str) -> str:
    return (
        name.lower()
        .replace("'", "")
        .replace("’", "")
        .replace("-", " ")
        .replace("/", " ")
        .strip()
    )


# alias comuni accettati per ogni regione
_ALIASES = {
    "Valle d'Aosta/Vallée d'Aoste": ["valle daosta", "valle d aosta", "vallee daoste", "aosta"],
    "Trentino-Alto Adige/Südtirol": ["trentino alto adige", "trentino", "alto adige", "sudtirol", "südtirol"],
    "Friuli-Venezia Giulia": ["friuli venezia giulia", "friuli"],
    "Emilia-Romagna": ["emilia romagna"],
}


def resolve_region(query: str):
    """Trova una regione data una stringa libera. Ritorna (codice, nome) o None."""
    if not query:
        return None
    q = _normalize(query)

    # match esatto sul codice ISTAT
    for code, name in REGIONS:
        if q == code:
            return code, name

    # match esatto sul nome normalizzato
    for code, name in REGIONS:
        if _normalize(name) == q:
            return code, name

    # alias
    for code, name in REGIONS:
        for alias in _ALIASES.get(name, []):
            if q == alias:
                return code, name

    # match parziale (la query è contenuta nel nome o viceversa)
    for code, name in REGIONS:
        n = _normalize(name)
        if q in n or n in q:
            return code, name
        for alias in _ALIASES.get(name, []):
            if q in alias or alias in q:
                return code, name
    return None


def list_regions():
    return list(REGIONS)
