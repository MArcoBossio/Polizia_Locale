"""Simple SQLite-backed persistent cache for fetched pages.

Stores (url, status, final_url, text, ts). Intended as an optional
cross-run cache to reduce repeated HTTP requests.
"""
from __future__ import annotations

import sqlite3
import time
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple


class SQLiteCache:
    def __init__(self, db_path: str | Path | None = None, ttl_seconds: Optional[int] = 7 * 24 * 3600) -> None:
        """Create a persistent SQLite-backed page cache.

        Args:
            db_path: optional path to the sqlite file. Defaults to ./cache/polizia_locale_cache.sqlite
            ttl_seconds: optional TTL for cached pages in seconds. If None, entries never expire by TTL.
        """
        if db_path is None:
            # Allow override via env var for CI/tests or custom locations.
            env_path = os.environ.get("POLIZIA_LOCALE_CACHE_PATH")
            if env_path:
                db_path = Path(env_path)
                db_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                # Default to a per-user cache directory. This keeps cache out of
                # the repository and available across runs for performance.
                base = Path.home() / ".polizia_locale_cache"
                base.mkdir(parents=True, exist_ok=True)
                db_path = base / "polizia_locale_cache.sqlite"
        self._db_path = str(db_path)
        # TTL in seconds (None => no TTL)
        self.ttl_seconds = int(ttl_seconds) if ttl_seconds is not None else None
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self._db_path, timeout=5)

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_cache (
                    url TEXT PRIMARY KEY,
                    status INTEGER,
                    final_url TEXT,
                    text TEXT,
                    ts REAL
                )
                """
            )

    def get(self, url: str) -> Optional[Tuple[int, str, str, float]]:
        with self._conn() as conn:
            row = conn.execute("SELECT status, final_url, text, ts FROM page_cache WHERE url = ?", (url,)).fetchone()
            if not row:
                return None
            status, final_url, text, ts = int(row[0]), row[1], row[2], float(row[3])
            if self.ttl_seconds is not None:
                if (time.time() - ts) > self.ttl_seconds:
                    # expired
                    return None
            return status, final_url, text, ts

    def set(self, url: str, status: int, final_url: str, text: str) -> None:
        ts = time.time()
        with self._conn() as conn:
            conn.execute(
                "REPLACE INTO page_cache(url, status, final_url, text, ts) VALUES (?, ?, ?, ?, ?)",
                (url, int(status), final_url, text, ts),
            )

    def purge_older(self, older_than_seconds: int) -> int:
        """Delete rows older than `older_than_seconds` seconds. Returns number of rows deleted."""
        cutoff = time.time() - older_than_seconds
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM page_cache WHERE ts < ?", (cutoff,))
            return cur.rowcount

    def compact(self, max_entries: int) -> int:
        """Keep at most `max_entries` newest rows; delete older ones. Returns number deleted."""
        if max_entries <= 0:
            return 0
        with self._conn() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM page_cache")
            total = cur.fetchone()[0]
            if total <= max_entries:
                return 0
            # delete oldest rows beyond max_entries
            cur = conn.execute(
                "DELETE FROM page_cache WHERE url IN (SELECT url FROM page_cache ORDER BY ts ASC LIMIT ?)",
                (total - max_entries,),
            )
            return cur.rowcount
