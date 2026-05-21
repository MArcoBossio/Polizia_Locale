"""Fast helpers for the scraper: link extraction and lightweight parsing wrappers.
"""
from . import (
    _candidate_links,
    _broad_candidate_links,
    _extract_emails_with_context,
    _enqueue_candidate_pages,
)

__all__ = [
    "_candidate_links",
    "_broad_candidate_links",
    "_extract_emails_with_context",
    "_enqueue_candidate_pages",
]
