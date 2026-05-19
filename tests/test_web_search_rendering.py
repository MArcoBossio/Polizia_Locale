from __future__ import annotations

import asyncio

from polizia_locale.web_search import _aggressive_render_html


class _FakeNode:
    def __init__(self, label: str, on_click=None):
        self._label = label
        self._on_click = on_click

    async def inner_text(self, timeout=0):
        return self._label

    async def click(self, timeout=0):
        if self._on_click is not None:
            self._on_click()


class _FakeLocator:
    def __init__(self, page, selector: str):
        self.page = page
        self.selector = selector

    async def count(self):
        if self.selector in {"button", "summary", "[role='button']", "details > summary"}:
            return 1
        return 0

    def nth(self, idx: int):
        return _FakeNode("Mostra contatti", on_click=lambda: setattr(self.page, "expanded", True))

    async def inner_text(self, timeout=0):
        if self.selector == "body":
            return self.page.body_text()
        return ""


class _FakePage:
    def __init__(self):
        self.expanded = False
        self.loads: list[str] = []

    async def content(self):
        return "<html><body><button>Mostra contatti</button></body></html>"

    def locator(self, selector: str):
        return _FakeLocator(self, selector)

    async def wait_for_load_state(self, state: str, timeout: int):
        self.loads.append(state)

    async def wait_for_timeout(self, ms: int):
        return None

    async def evaluate(self, script: str):
        return None

    def body_text(self):
        if self.expanded:
            return "Polizia Locale - Contatti: polizialocale@comune.example.it"
        return "Polizia Locale - Contatti nascosti"


def test_aggressive_render_html_expands_js_controls():
    page = _FakePage()

    rendered = asyncio.run(_aggressive_render_html(page, timeout_ms=1000))

    assert "polizialocale@comune.example.it" in rendered
    assert "networkidle" in page.loads