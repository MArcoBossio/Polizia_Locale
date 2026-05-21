"""Heavy helpers for the scraper: Playwright/browser fallback and OCR helpers.
"""
from . import (
    _should_try_browser_fallback,
    _browser_rendered_pairs,
    _browser_rendered_text,
    _ocr_page_screenshot,
)

__all__ = [
    "_should_try_browser_fallback",
    "_browser_rendered_pairs",
    "_browser_rendered_text",
    "_ocr_page_screenshot",
]
