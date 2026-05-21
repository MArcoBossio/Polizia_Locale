from __future__ import annotations

import asyncio
from types import SimpleNamespace

from polizia_locale import scraper
from polizia_locale.scraper import async_scraper


class _FakeSession:
    def __init__(self, responses: dict[str, SimpleNamespace]):
        self._responses = responses
        self.calls: list[str] = []

    def get(self, url: str, timeout=None, allow_redirects=True):
        self.calls.append(url)
        response = self._responses.get(url)
        if response is None:
            raise AssertionError(f"unexpected URL requested: {url}")
        return response


class _DummyAsyncClientSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_scrape_polizia_locale_uses_full_site_hint_and_rendered_text(monkeypatch):
    site_hint = (
        "https://www.comune.prato.it/it/amministrazione/uffici/"
        "comando-polizia-municipale/archivio61_0_135.html"
    )

    fake_session = _FakeSession(
        {
            site_hint: SimpleNamespace(
                status_code=200,
                text="<html><body><h1>Comando della Polizia Locale</h1></body></html>",
                url=site_hint,
            )
        }
    )

    monkeypatch.setattr(scraper, "_new_session", lambda: fake_session)
    monkeypatch.setattr(
        scraper,
        "find_comune_website",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("site search should not run")),
    )
    monkeypatch.setattr(
        scraper,
        "_browser_rendered_text",
        lambda url, timeout_ms=15000: "Contatti Email polizialocale@comune.prato.it",
    )
    monkeypatch.setattr(scraper, "_ocr_page_screenshot", lambda *args, **kwargs: "")

    result = scraper.scrape_polizia_locale(
        "Prato",
        "Prato",
        "047015",
        site_hint=site_hint,
        timeout=5,
        total_budget=10,
        strict_pl_local=True,
        pdf_extract=False,
    )

    assert result is not None
    assert result.email == "polizialocale@comune.prato.it"
    assert result.pagina == site_hint


def test_scrape_polizia_locale_falls_back_on_broad_links(monkeypatch):
    site_hint = "https://www.example.com/"
    homepage = "https://www.example.com/"
    assistance = "https://www.example.com/assistenza"

    fake_session = _FakeSession(
        {
            homepage: SimpleNamespace(
                status_code=200,
                text=(
                    "<html><body>"
                    '<a href="/assistenza">Assistenza</a>'
                    "</body></html>"
                ),
                url=homepage,
            ),
            assistance: SimpleNamespace(
                status_code=200,
                text=(
                    "<html><body>"
                    "<h1>Assistenza</h1>"
                    "<p>Contatti: polizialocale@comune.prato.it</p>"
                    "</body></html>"
                ),
                url=assistance,
            ),
        }
    )

    monkeypatch.setattr(scraper, "_new_session", lambda: fake_session)
    monkeypatch.setattr(scraper, "find_comune_website", lambda *args, **kwargs: homepage)
    monkeypatch.setattr(
        scraper,
        "_browser_rendered_pairs",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("browser fallback should not run on generic homepage")),
    )
    monkeypatch.setattr(
        scraper,
        "_browser_rendered_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("browser fallback should not run on generic homepage")),
    )
    monkeypatch.setattr(
        scraper,
        "_ocr_page_screenshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ocr fallback should not run on generic homepage")),
    )

    result = scraper.scrape_polizia_locale(
        "Prato",
        "Prato",
        "047015",
        site_hint=site_hint,
        timeout=5,
        total_budget=10,
        strict_pl_local=True,
        pdf_extract=False,
    )

    assert result is not None
    assert result.email == "polizialocale@comune.prato.it"
    assert result.pagina == assistance
    assert assistance in fake_session.calls


def test_scrape_polizia_locale_strict_skips_info_and_keeps_searching(monkeypatch):
    base = "https://www.example.com"
    page_generic = f"{base}/polizia-locale"
    page_specific = f"{base}/comando-polizia-locale"

    fake_session = _FakeSession(
        {
            page_generic: SimpleNamespace(
                status_code=200,
                text=(
                    "<html><body>"
                    "<h1>Polizia Locale</h1>"
                    "<p>Contatti: info@comune.example.com</p>"
                    "</body></html>"
                ),
                url=page_generic,
            ),
            page_specific: SimpleNamespace(
                status_code=200,
                text=(
                    "<html><body>"
                    "<h1>Comando di Polizia Locale</h1>"
                    "<p>Email: polizia.locale@comune.example.com</p>"
                    "</body></html>"
                ),
                url=page_specific,
            ),
        }
    )

    monkeypatch.setattr(scraper, "_new_session", lambda: fake_session)
    monkeypatch.setattr(scraper, "find_comune_website", lambda *args, **kwargs: base)
    monkeypatch.setattr(scraper, "_browser_rendered_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(scraper, "_ocr_page_screenshot", lambda *args, **kwargs: "")
    monkeypatch.setattr(scraper, "_DIRECT_PATH_HINTS", ("/polizia-locale", "/comando-polizia-locale"))

    result = scraper.scrape_polizia_locale(
        "Esempio",
        "Trento",
        "000001",
        site_hint=base,
        timeout=5,
        total_budget=10,
        strict_pl_local=True,
        pdf_extract=False,
    )

    assert result is not None
    assert result.email == "polizia.locale@comune.example.com"
    assert page_generic in fake_session.calls
    assert page_specific in fake_session.calls


def test_dom_context_beats_distant_negative_hints():
        html = """
        <html>
            <head><title>Comune di Test</title></head>
            <body>
                <nav class="breadcrumb">Home / Servizi / Polizia Locale</nav>
                <aside><p>anagrafe protocollo tributi</p></aside>
                <section>
                    <h2>Servizio associato di Polizia Locale</h2>
                    <p>Email: vigili@cm-test.vda.it</p>
                </section>
            </body>
        </html>
        """

        pairs = scraper._extract_emails_with_context(html)

        assert pairs
        email, ctx = pairs[0]
        score, reasons = scraper._score_email_context(html, email, ctx)

        assert email == "vigili@cm-test.vda.it"
        assert "servizio associato di polizia locale" in ctx.lower()
        assert score >= 4
        assert any(reason in reasons for reason in ("context_polizia", "context_fuzzy_polizia", "dom_heading_pl"))

def test_scrape_polizia_locale_uses_structured_browser_fallback(monkeypatch):
    base = "https://www.example.com"

    fake_session = _FakeSession(
        {
            base: SimpleNamespace(
                status_code=200,
                text="<html><body><h1>Comune di Esempio</h1></body></html>",
                url=base,
            )
        }
    )

    monkeypatch.setattr(scraper, "_new_session", lambda: fake_session)
    monkeypatch.setattr(scraper, "find_comune_website", lambda *args, **kwargs: base)
    monkeypatch.setattr(scraper, "_browser_rendered_pairs", lambda *args, **kwargs: [("vigili@comune.example.com", "Servizio associato di Polizia Locale | vigili@comune.example.com")])
    monkeypatch.setattr(scraper, "_browser_rendered_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(scraper, "_ocr_page_screenshot", lambda *args, **kwargs: "")

    result = scraper.scrape_polizia_locale(
        "Esempio",
        "Trento",
        "000001",
        site_hint=base,
        timeout=5,
        total_budget=10,
        strict_pl_local=True,
        pdf_extract=False,
    )

    assert result is not None
    assert result.email == "vigili@comune.example.com"


def test_async_scrape_polizia_locale_accepts_emails(monkeypatch):
    base = "https://www.example.com"
    homepage = f"{base}/"

    class _DummyCache:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url):
            return None

        def set(self, url, status, final_url, text):
            return None

    async def _fake_get_page_async(session, url, timeout, page_cache, persistent):
        if url == homepage or url == base:
            return 200, homepage, (
                "<html><body><h1>Comando di Polizia Locale</h1>"
                "<p>Contatti: polizialocale@comune.example.com</p></body></html>"
            )
        return 404, url, ""

    async def _fake_fetch_many_async(session, urls, timeout):
        return []

    monkeypatch.setattr(async_scraper.aiohttp, "ClientSession", lambda *args, **kwargs: _DummyAsyncClientSession())
    monkeypatch.setattr(async_scraper, "SQLiteCache", _DummyCache)
    monkeypatch.setattr(async_scraper, "find_comune_website", lambda *args, **kwargs: base)
    monkeypatch.setattr(async_scraper, "_get_page_async", _fake_get_page_async)
    monkeypatch.setattr(async_scraper, "_fetch_many_async", _fake_fetch_many_async)
    monkeypatch.setattr(async_scraper, "_browser_rendered_pairs", lambda *args, **kwargs: [])
    monkeypatch.setattr(async_scraper, "_browser_rendered_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(async_scraper, "_ocr_page_screenshot", lambda *args, **kwargs: "")

    result = asyncio.run(
        async_scraper.async_scrape_polizia_locale(
            "Esempio",
            "Trento",
            "000001",
            site_hint=base,
            timeout=5,
            total_budget=10,
            strict_pl_local=True,
            pdf_extract=False,
        )
    )

    assert result is not None
    assert result.email == "polizialocale@comune.example.com"


def test_sibling_heading_context_scores_email():
    """Heading in sibling should be picked up by DOM context and boost score."""
    html = """
    <html>
      <body>
        <h3>Servizio Associato di Vigilanza</h3>
        <div>
          <a href="mailto:vigili@cm-test.vda.it"></a>
        </div>
      </body>
    </html>
    """

    pairs = scraper._extract_emails_with_context(html)

    assert pairs
    email, ctx = pairs[0]
    score, reasons = scraper._score_email_context(html, email, ctx)

    assert email == "vigili@cm-test.vda.it"
    assert "servizio associato di vigilanza" in ctx.lower()
    assert score >= 3
    assert any(reason in reasons for reason in ("context_fuzzy_polizia", "sibling_context_pl", "anchor_context_pl"))