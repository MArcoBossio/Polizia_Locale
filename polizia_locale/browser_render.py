from __future__ import annotations

from bs4 import BeautifulSoup

from .utils import USER_AGENT


_INTERACTION_HINTS = (
    "contatt",
    "mostra",
    "altro",
    "leggi",
    "espandi",
    "uffici",
    "polizia",
    "vigili",
    "comando",
    "menu",
    "dettagli",
    "clicca",
)


def _open_image_safe(data: bytes):
    from io import BytesIO
    from PIL import Image

    im = Image.open(BytesIO(data))
    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("RGB")
    elif im.mode != "RGB":
        im = im.convert("RGB")
    return im


async def _aggressive_render_html(page, timeout_ms: int) -> str:
    async def _snapshot(tag: str) -> tuple[str, str, str]:
        html = ""
        body_text = ""
        try:
            html = await page.content()
        except Exception:
            html = ""
        try:
            body_text = await page.locator("body").inner_text(timeout=3000)
        except Exception:
            body_text = ""
        return tag, html, body_text

    async def _probe_controls() -> None:
        selectors = ("button", "summary", "[role='button']", "details > summary")
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
            except Exception:
                continue
            for idx in range(min(count, 12)):
                try:
                    item = locator.nth(idx)
                    label = await item.inner_text(timeout=600)
                except Exception:
                    continue
                if not label:
                    continue
                low = label.lower()
                if not any(h in low for h in _INTERACTION_HINTS):
                    continue
                try:
                    await item.click(timeout=800)
                    await page.wait_for_timeout(200)
                except Exception:
                    continue

    snapshots: list[tuple[str, str, str]] = []
    snapshots.append(await _snapshot("initial"))

    for load_state in ("domcontentloaded", "networkidle"):
        try:
            await page.wait_for_load_state(load_state, timeout=timeout_ms)
        except Exception:
            pass
        try:
            await page.wait_for_timeout(250)
        except Exception:
            pass
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(250)
            await page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        await _probe_controls()
        snapshots.append(await _snapshot(load_state))

    best_html = ""
    best_text = ""
    best_score = -1
    for _tag, html, body_text in snapshots:
        combined = f"{html} {body_text}".lower()
        score = len(body_text) + len(html)
        if any(token in combined for token in ("@", "polizia", "vigili", "comando", "contatti")):
            score += 1000
        if score > best_score:
            best_score = score
            best_html = html
            best_text = body_text

    return f"{best_html} {best_text}".strip()


def render_page_snapshot(
    url: str,
    timeout_ms: int = 15000,
    capture_screenshot: bool = False,
) -> tuple[str, str, bytes]:
    """Renderizza una pagina con Playwright e restituisce HTML, testo e screenshot opzionale."""

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return "", "", b""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=USER_AGENT,
                locale="it-IT",
                viewport={"width": 1440, "height": 2200},
            )
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(800)
            html = page.content()
            body_text = ""
            try:
                body_text = page.locator("body").inner_text(timeout=3000)
            except Exception:
                body_text = ""
            screenshot = page.screenshot(full_page=True) if capture_screenshot else b""
            browser.close()
        return html or "", body_text or "", screenshot or b""
    except Exception:
        return "", "", b""


def browser_rendered_text(url: str, timeout_ms: int = 15000) -> str:
    html, body_text, _img = render_page_snapshot(url, timeout_ms=timeout_ms, capture_screenshot=False)
    return body_text or html or ""


def browser_rendered_pairs(url: str, timeout_ms: int = 15000) -> list[tuple[str, str]]:
    from .scraper import _extract_emails_with_context

    html, _text, _img = render_page_snapshot(url, timeout_ms=timeout_ms, capture_screenshot=False)
    if not html:
        return []
    return _extract_emails_with_context(html)


def ocr_page_screenshot(url: str, timeout_ms: int = 15000) -> str:
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return ""

    try:
        _html, _text, img = render_page_snapshot(url, timeout_ms=timeout_ms, capture_screenshot=True)
    except Exception:
        return ""

    if not img:
        return ""

    try:
        screenshot = _open_image_safe(img)
        return pytesseract.image_to_string(screenshot, lang="ita")
    except Exception:
        return ""
