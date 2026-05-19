#!/usr/bin/env python3
"""Quick test for BraveSearchFinder: runs a single query if BRAVE_API_KEY is set."""
import os
import sys

def main() -> int:
    if not os.environ.get("BRAVE_API_KEY"):
        print("BRAVE_API_KEY not set; skipping Brave test.")
        return 0

    try:
        from polizia_locale.brave_search import BraveSearchFinder
    except Exception as e:
        print(f"Failed to import BraveSearchFinder: {e}")
        return 2

    print("[TEST] Creating BraveSearchFinder...")
    try:
        finder = BraveSearchFinder()
    except Exception as e:
        print(f"Could not create BraveSearchFinder: {e}")
        return 2

    try:
        print("[TEST] Searching Prato (deep=False, max 15s)...")
        pec, mail, sources = finder.search_polizia_locale("Prato", "Prato", deep=False, max_total_seconds=15.0)
        print(f"[TEST] PEC: {pec}")
        print(f"[TEST] Mail: {mail}")
        print(f"[TEST] Sources (sample): {sources[:3]}")
    finally:
        finder.close()
        print("[TEST] Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
