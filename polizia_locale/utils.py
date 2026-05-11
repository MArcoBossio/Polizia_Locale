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
