from __future__ import annotations

from types import SimpleNamespace

from polizia_locale import scraper


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
    assert fake_session.calls == [site_hint]


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
    monkeypatch.setattr(scraper, "_browser_rendered_text", lambda *args, **kwargs: "")
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
    assert result.pagina == assistance
    assert homepage in fake_session.calls
    assert assistance in fake_session.calls