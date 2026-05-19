#!/usr/bin/env python3
"""Test semplice di WebSearchFinder"""
from polizia_locale.web_search import WebSearchFinder

def main() -> int:
    print("[TEST] Creazione WebSearchFinder...")
    finder = WebSearchFinder()

    print("[TEST] Avvio browser...")
    finder.start()

    try:
        print("[TEST] Ricerca 'Prato'...")
        pec, mail = finder.search_polizia_locale("Prato", "Prato")
        print(f"[TEST] Risultati PEC: {pec}")
        print(f"[TEST] Risultati Mail: {mail}")
    finally:
        print("[TEST] Arresto browser...")
        finder.stop()
        print("[TEST] Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
