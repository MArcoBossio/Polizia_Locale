#!/usr/bin/env python3
"""Entry point per eseguire lo script senza installarlo come pacchetto.

Esempi:
    python run.py Lombardia
    python run.py 03 -o ./output
    python run.py --list-regions
    python run.py                 # modalità interattiva con menu
"""
from polizia_locale.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
