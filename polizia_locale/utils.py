"""Funzioni di utilità: cache su disco e download HTTP."""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

CACHE_DIR = Path(
    os.environ.get("POLIZIA_LOCALE_CACHE", str(Path.home() / ".cache" / "polizia_locale"))
)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "polizia-locale-finder/1.0"
)


def cached_path(filename: str) -> Path:
    return CACHE_DIR / filename


def download(url: str, dest: Path, max_age_hours: int = 24, force: bool = False) -> Path:
    """Scarica un file e lo salva in cache. Se già presente e fresco, riusa."""
    dest = Path(dest)
    if (
        not force
        and dest.exists()
        and (time.time() - dest.stat().st_mtime) < max_age_hours * 3600
    ):
        return dest

    headers = {"User-Agent": USER_AGENT}
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
        tmp.replace(dest)
    return dest


FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "yahoo.com",
    "libero.it",
    "virgilio.it",
    "outlook.com",
    "tin.it",
    "tiscali.it",
    "alice.it",
    "hotmail.it",
    "icloud.com",
    "fastwebnet.it",
}


def is_likely_personal_email(email: str) -> bool:
    """Heuristics: True se l'email sembra essere personale (provider free o local-part nome.cognome).

    Questo è un filtro euristico, non perfetto. Usa insieme a conferme sul sito.
    """
    import re

    if not email or "@" not in email:
        return False
    local, domain = email.split("@", 1)
    domain = domain.lower()
    local = local.lower()
    # domini noti di posta personale
    for d in FREE_EMAIL_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return True

    # pattern nome.cognome o nome_cognome -> probabile personale
    if ("." in local or "_" in local) and all(part.isalpha() and 1 < len(part) < 25 for part in re.split(r"[._]", local) if part):
        return True

    # local molto breve + digit (es. mrossi123) -> probabile personale
    if re.search(r"[a-z]{1,3}\d+", local):
        return True

    return False
