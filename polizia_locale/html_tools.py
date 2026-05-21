from __future__ import annotations

from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:
    from selectolax.lexbor import LexborHTMLParser as _FastHTMLParser  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    try:
        from selectolax.parser import HTMLParser as _FastHTMLParser  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - optional dependency
        _FastHTMLParser = None


def _fast_parser(html: str):
    if not html or _FastHTMLParser is None:
        return None
    try:
        return _FastHTMLParser(html)
    except Exception:
        return None


def html_text(html: str) -> str:
    parser = _fast_parser(html)
    if parser is not None:
        try:
            body = getattr(parser, "body", None)
            if body is not None:
                text = body.text()
                if text:
                    return text
            root = getattr(parser, "root", None)
            if root is not None:
                text = root.text()
                if text:
                    return text
        except Exception:
            pass
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        return soup.get_text(" ", strip=True)
    except Exception:
        return html or ""


def iter_anchors(html: str) -> Iterator[tuple[str, str]]:
    parser = _fast_parser(html)
    if parser is not None:
        try:
            for node in parser.css("a[href]"):
                attrs = getattr(node, "attributes", {}) or {}
                href = str(attrs.get("href", "")).strip()
                text = (node.text() or "").strip()
                if href:
                    yield href, text
            return
        except Exception:
            pass

    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").strip()
        if href:
            yield href, text


def iter_images(html: str) -> Iterator[tuple[str, str]]:
    parser = _fast_parser(html)
    if parser is not None:
        try:
            for node in parser.css("img[src]"):
                attrs = getattr(node, "attributes", {}) or {}
                src = str(attrs.get("src", "")).strip()
                alt = str(attrs.get("alt", "")).strip()
                if src:
                    yield src, alt
            return
        except Exception:
            pass

    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return
    for img in soup.find_all("img", src=True):
        src = img.get("src", "")
        alt = (img.get("alt") or "").strip()
        if src:
            yield src, alt
